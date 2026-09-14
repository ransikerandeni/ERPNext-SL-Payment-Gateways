"""People's Bank (peoplesbank.lk) IPG - CyberSource Secure Acceptance Hosted Checkout.

People's Bank does not run a gateway protocol of its own: their IPG is a
branded CyberSource (Visa Acceptance) merchant profile, and the
integration they issue after onboarding is the standard *Secure
Acceptance Hosted Checkout* one. What the bank supplies is a profile id,
an access key and a secret key; the protocol itself is documented in
CyberSource's own "Secure Acceptance Hosted Checkout Integration" guide,
a copy of which ships in the bank's integration pack alongside their PHP
sample (`CYBSPEBBasic.php`).

The scheme, in full:

  * Checkout is an HTTPS POST of name/value fields to the Secure
    Acceptance endpoint. `signed_field_names` names, in order, the fields
    covered by the signature; the signature is
    base64(HMAC-SHA256(secret_key, "name=value,name=value,...")) over
    exactly those fields in exactly that order. `unsigned_field_names`
    names the rest.
  * The response comes back the same way - a POST to the receipt/cancel
    page, and (if configured) to the back-office POST URL - signed with
    the *same* secret key over the fields its own `signed_field_names`
    lists. Verification is recomputing that HMAC.

Two consequences worth stating plainly, because they set this gateway
apart from the other two implemented here:

  1. `merchant_verified` is True. The secret key is issued to our
     merchant profile alone, so a response that verifies was produced by
     CyberSource for *our* profile - unlike WebXPay, where the signing
     key is shared across merchants. `req_profile_id` is checked against
     our settings on top of that, which also means a sandbox response
     replayed at a live site is rejected twice over.
  2. The amount and currency come back inside the signed set
     (`auth_amount` when the authorisation succeeded, `req_amount`
     otherwise), so a caller can genuinely price-check a payment here.

Fields not listed in the response's own `signed_field_names` are
discarded before anything is read - CyberSource signs every field it
sends, so an unsigned field in the POST is by definition something the
sender added, and their guide says to ignore it. `raw` therefore holds
the signed fields only, not `form_dict`.

Signed vs unsigned on the way out follows the bank's own sample: the
billing details and `auth_trans_ref_no` are unsigned, everything that
decides what is charged (amount, currency, reference_number,
transaction_type, profile_id, access_key, transaction_uuid,
signed_date_time) is signed, and so are the `override_*` URLs - the
guide explicitly calls out `override_custom_receipt_page` as a field
that must be signed to prevent tampering.

Settings ("Peoples Bank Settings" single doctype) hold sandbox and live
credential sets, selected by `use_sandbox` - the bank issues a test
profile on the CyberSource test host and a separate live one. They also
hold `allow_lkr`, which decides whether this gateway will sign a checkout
in LKR at all; see supported_currencies() below. See docs/peoples_bank.md.

RESIDUAL RISKS you must handle in your own return handler
---------------------------------------------------------
1. There is no nonce in the response, so it can be replayed. Make your
   handler idempotent and ignore responses for orders already settled.
   (`req_transaction_uuid` is unique per *attempt* and is returned in the
   signed set, so it is a usable de-duplication key if you store it.)
2. A verified response proves what CyberSource recorded, not what your
   order should have cost. Compare `amount`/`currency` against your own
   record before settling.
3. `decision=REVIEW` maps to "Pending", not "Paid": the authorisation was
   declined but may still be capturable, so the money is not yours yet.
   Do not ship anything on a REVIEW.
"""

import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import frappe

from sl_payment_gateways.utils import (
	clean_text,
	constant_time_equals,
	format_amount,
	is_sandbox,
	mode_password,
	mode_value,
	site_url,
	validate_currency,
	validate_order_id,
)

# CyberSource documents reference_number as String(50).
MAX_ORDER_ID_LENGTH = 50

# The Secure Acceptance Hosted Checkout endpoints (guide, "Endpoints and
# Transaction Types"). The bank's test profile lives on the first, the
# live one on the second. A merchant issued a bank-hosted front end
# instead - the QR "middle page" at egateway.peoplesbank.lk that their
# alternate sample posts to - can override these per mode in Settings.
SANDBOX_CHECKOUT_URL = "https://testsecureacceptance.cybersource.com/pay"
LIVE_CHECKOUT_URL = "https://secureacceptance.cybersource.com/pay"

# WHAT THIS GATEWAY WILL CHARGE IN.
#
# USD always; LKR only once "Allow LKR Charges" is ticked in Settings.
#
# The gate is not caution for its own sake. People's Bank has to enable LKR on
# the CyberSource profile itself, and until they do the whole checkout dies at
# the hosted page with decision=ERROR, reason_code=102, invalid_fields=currency
# - the payer gets there, sees a card form, and cannot pay. An option that
# always fails is worse than no option, so LKR stays off until somebody has
# confirmed with the bank and put one LKR payment through the sandbox.
#
# Order is the declared preference, used only where a caller has no better
# basis for choosing. It has one: charging in the currency the fee is already
# priced in avoids a conversion entirely, and create_gateway_payment picks that
# way. USD leads here purely because it is the one that is always available.
BASE_CURRENCY = "USD"
GATED_CURRENCY = "LKR"

# Static fallback for a caller that cannot reach Settings (see
# api.gateway_currencies). Never wider than what supported_currencies() allows.
CURRENCIES = (BASE_CURRENCY,)


def supported_currencies():
	"""The currencies a checkout may be built in right now, preferred first."""
	settings = _settings()

	# .get() rather than attribute access: a site running this code before the
	# doctype has been migrated has no `allow_lkr` field at all, and that must
	# read as off rather than raising mid-checkout.
	if settings.get("allow_lkr"):
		return [BASE_CURRENCY, GATED_CURRENCY]

	return [BASE_CURRENCY]


# A single-message sale: authorise and submit for settlement in one step.
# ("authorization" would need a separate capture through the Simple Order
# API, which this app does not implement.)
TRANSACTION_TYPE = "sale"

LOCALE = "en"

# Signed on the way out, in this order - the order is part of the
# signature, so it must not be rearranged. Everything here is generated
# by this app; nothing the payer types is in it.
SIGNED_FIELDS = (
	"access_key",
	"profile_id",
	"transaction_uuid",
	"signed_field_names",
	"unsigned_field_names",
	"signed_date_time",
	"locale",
	"transaction_type",
	"reference_number",
	"amount",
	"currency",
)

# Where the customer's browser is sent afterwards, and the server-to-
# server notification. Appended to the signed list only when the caller
# supplies them - a name in signed_field_names whose field is absent
# signs an empty value and breaks verification at CyberSource's end.
OVERRIDE_FIELDS = (
	# customer.return_url  - the receipt page, where the signed response
	#                        is POSTed to the browser.
	"override_custom_receipt_page",
	# customer.cancel_url  - where "cancel" on the hosted page lands.
	"override_custom_cancel_page",
	# customer.notify_url  - the back-office POST, which does not depend
	#                        on the browser surviving the redirect.
	"override_backoffice_post_url",
)

# Left unsigned, matching the bank's own sample: these are billing
# details the hosted page may also collect from the payer, and none of
# them changes what is charged.
UNSIGNED_FIELDS = (
	"auth_trans_ref_no",
	"bill_to_forename",
	"bill_to_surname",
	"bill_to_email",
	"bill_to_address_line1",
	"bill_to_address_city",
	"bill_to_address_country",
)

# Named in unsigned_field_names only when we actually have a value.
#
# CyberSource reads a field that is *named* in signed/unsigned_field_names
# but carries an empty value as supplied-and-invalid rather than as
# omitted, and rejects the request (reason_code 102). People's Bank
# support put it plainly: "in the last request these are sent with null
# values - if you are not sending it drop it."
#
# They are not optional everywhere, though: the bank requires both once
# the billing country is not LK, and CyberSource requires them for US and
# Canadian billing addresses regardless. Hence the check in
# build_checkout() rather than a silent omission.
OPTIONAL_BILLING_FIELDS = (
	"bill_to_address_state",
	"bill_to_address_postal_code",
)

# The billing country assumed when the payer record does not carry a
# usable two-letter code, and the one country for which the bank does not
# insist on state and postal code.
DEFAULT_COUNTRY = "LK"

# CyberSource's own test billing address, handed out in the sandbox
# activation mail ("use the standard dummy billing information if you are
# not using real life data"). Their test environment is shared, and its
# fraud and AVS rules are tuned for this address - a real Sri Lankan one
# can be declined there for reasons that would never apply live, which is
# exactly the kind of false signal that wastes a day of testing.
#
# Used only when `use_test_billing_data` is ticked in Settings AND the
# sandbox is active. The sandbox condition is not redundant: it is what
# makes it impossible to charge a live card against a Mountain View
# address because somebody left a checkbox on.
#
# Note this replaces the payer's name and email too. That is the point -
# it is a complete stand-in, not a partial one, and a half-real address is
# neither testable nor honest.
TEST_BILLING_ADDRESS = {
	"bill_to_forename": "noreal",
	"bill_to_surname": "name",
	"bill_to_email": "null@cybersource.com",
	"bill_to_address_line1": "1295 Charleston Rd",
	"bill_to_address_city": "Mountain View",
	"bill_to_address_state": "CA",
	"bill_to_address_country": "US",
	"bill_to_address_postal_code": "94043",
}

# decision -> our status. From the guide's "Types of Notifications" table.
#   ACCEPT  - settled (reason codes 100, 110).
#   REVIEW  - authorisation declined, capture *might* still be possible.
#             Not money in hand, so Pending rather than Paid.
#   DECLINE - declined by the processor or by fraud settings.
#   CANCEL  - the payer backed out.
#   ERROR   - access denied, page not found, or a server error.
# Anything unrecognised falls through to Failed: the guide's own advice
# is to decide on `decision` when the reason code is unknown, and an
# unknown *decision* is not something to treat as paid.
STATUS_BY_DECISION = {
	"ACCEPT": "Paid",
	"REVIEW": "Pending",
	"DECLINE": "Failed",
	"CANCEL": "Failed",
	"ERROR": "Failed",
}

# The response fields this app reads, all of which CyberSource includes
# in its own signed set. Read from the *verified* subset only.
DECISION_FIELD = "decision"
REFERENCE_FIELD = "req_reference_number"

# A response listing an implausible number of signed fields is malformed,
# not merely unusual. Real ones run to ~55 names; the cap only stops a
# forged payload turning verification into unbounded work.
MAX_SIGNED_FIELDS = 250


def _settings():
	try:
		return frappe.get_doc("Peoples Bank Settings")
	except frappe.DoesNotExistError:
		frappe.throw(
			"Peoples Bank Settings doctype does not exist - create it before using People's Bank."
		)


def _profile_id(settings):
	return str(mode_value(settings, "profile_id", is_sandbox(settings)))


def _access_key(settings):
	return str(mode_value(settings, "access_key", is_sandbox(settings)))


def _secret_key(settings):
	"""The HMAC key, for the active mode. Kept in its own function so the
	rest of the module can be read without it, and so verify_response()
	touches the credential store exactly once."""
	return mode_password(settings, "secret_key", is_sandbox(settings))


def _checkout_url(settings):
	"""The Secure Acceptance endpoint for the active mode.

	Overridable because People's Bank fronts some merchants with their own
	hosted page (the QR "middle page") that takes the same signed fields
	at a different URL. Unset - the normal case - means CyberSource's own
	endpoint for the mode.
	"""
	sandbox = is_sandbox(settings)
	prefix = "sandbox_" if sandbox else "live_"

	url = settings.get(prefix + "checkout_url") or settings.get("checkout_url")
	url = url.strip() if url else ""

	if not url:
		return SANDBOX_CHECKOUT_URL if sandbox else LIVE_CHECKOUT_URL

	parsed = urlparse(url)
	if parsed.scheme != "https" or not parsed.netloc:
		frappe.throw(
			"Peoples Bank Settings holds an invalid `%scheckout_url`: it must be an https:// URL. "
			"Leave it blank to use CyberSource's own endpoint." % (prefix,)
		)

	return url


def _https_site_url(url, label):
	"""site_url() plus CyberSource's own https-only rule for override URLs.

	Their guide requires these to be HTTPS with TLS 1.2+. Sending an http
	URL does not fail at post time - it fails later, as a receipt page the
	payer never reaches or a notification that never arrives, which is
	exactly the kind of silent breakage worth refusing up front.
	"""
	resolved = site_url(url, label)

	if not resolved.lower().startswith("https://"):
		frappe.throw(
			"Invalid %s: People's Bank (CyberSource) requires an https:// URL, got %s. "
			"Serve this site over HTTPS, or set your site's host_name to its https URL."
			% (label, frappe.utils.escape_html(resolved))
		)

	return resolved


def _country_code(value):
	"""A two-letter ISO country code, or LK.

	bill_to_address_country is String(2), so simply truncating whatever
	the caller passed turns "Sri Lanka" into "Sr" and CyberSource rejects
	the request. Anything that is not already a two-letter code falls back
	to the default rather than being mangled into an invalid one.
	"""
	code = str(value or "").strip().upper()

	return code if len(code) == 2 and code.isalpha() else DEFAULT_COUNTRY


def _sign(data: str, secret_key: str) -> str:
	"""base64(HMAC-SHA256(secret_key, data)) - the scheme's `signData()`."""
	digest = hmac.new(secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()
	return base64.b64encode(digest).decode("ascii")


def _data_to_sign(params, field_names) -> str:
	"""`name=value` pairs, comma-joined, in the order given.

	The order is the signature, so it comes from `field_names` (i.e. from
	signed_field_names) rather than from the dict. A name with no value
	signs as empty, which is what the reference PHP does when a signed
	field is missing from the POST - and a present-but-None value is the
	same thing, not the literal string "None".
	"""
	return ",".join(
		"%s=%s" % (name, "" if params.get(name) is None else params.get(name)) for name in field_names
	)


def build_checkout(order_id, amount, currency, customer):
	order_id = validate_order_id(order_id, max_length=MAX_ORDER_ID_LENGTH)
	amount = format_amount(amount)
	currency = validate_currency(currency)

	settings = _settings()

	# Defence in depth. create_gateway_payment validates the payer's choice
	# against this same list before it gets here, but build_checkout is what
	# actually signs the amount-and-currency pair, so it does not take the
	# caller's word for which currencies the profile accepts.
	allowed = supported_currencies()

	if currency not in allowed:
		frappe.throw(
			"People's Bank cannot be charged in %s. Accepted: %s. "
			"(LKR is available once People's Bank has enabled it on the CyberSource "
			"profile and \"Allow LKR Charges\" is ticked in Peoples Bank Settings.)"
			% (frappe.utils.escape_html(currency), ", ".join(allowed))
		)

	# Both conditions, and the sandbox one is the load-bearing half: a
	# ticked checkbox must never be able to send Mountain View to a live
	# card. An absent field (the app updated, the doctype not yet migrated)
	# reads as off.
	use_test_billing = bool(is_sandbox(settings)) and bool(settings.get("use_test_billing_data"))

	if use_test_billing:
		# Wholesale, including the name and email - see TEST_BILLING_ADDRESS.
		# No country check: the test address carries a state and a postal
		# code already, so there is nothing the rule below would catch.
		billing = dict(TEST_BILLING_ADDRESS)
	else:
		country = _country_code(customer.get("country"))
		state = clean_text(customer.get("state"), 20)
		postal_code = clean_text(customer.get("postal_code"), 10)

		# Refused here rather than sent and declined at CyberSource: outside
		# Sri Lanka the bank requires both, so a checkout built without them
		# is a hosted page the payer can only fail on.
		if country != DEFAULT_COUNTRY and not (state and postal_code):
			frappe.throw(
				"People's Bank needs a billing state/province and postal code for a billing "
				"address outside Sri Lanka (country %s). Record both against the payer before "
				"starting the checkout." % (frappe.utils.escape_html(country),)
			)

		billing = {
			# CyberSource's hosted page collects billing details itself, but a
			# profile configured to require them still needs something to
			# prefill - these placeholders match the bank's sample pack.
			"bill_to_forename": clean_text(customer.get("first_name"), 60, "Customer"),
			"bill_to_surname": clean_text(customer.get("last_name"), 60, "-"),
			"bill_to_email": clean_text(customer.get("email"), 255, "null@cybersource.com"),
			"bill_to_address_line1": clean_text(
				customer.get("address") or customer.get("organization"), 60, "N/A"
			),
			"bill_to_address_city": clean_text(customer.get("city"), 50, "Colombo"),
			"bill_to_address_country": country,
			"bill_to_address_state": state,
			"bill_to_address_postal_code": postal_code,
		}

	fields = {
		"access_key": _access_key(settings),
		"profile_id": _profile_id(settings),
		# Unique per attempt; CyberSource uses it to detect duplicate
		# submissions, and returns it as req_transaction_uuid.
		"transaction_uuid": uuid.uuid4().hex,
		"signed_date_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"locale": LOCALE,
		"transaction_type": TRANSACTION_TYPE,
		"reference_number": order_id,
		"amount": amount,
		"currency": currency,
		# Reconciliation reference the bank shows against the transaction.
		"auth_trans_ref_no": order_id,
	}

	for name, value in billing.items():
		# State and postal code are dropped when blank rather than sent
		# empty - see OPTIONAL_BILLING_FIELDS. Everything else always goes.
		if value or name not in OPTIONAL_BILLING_FIELDS:
			fields[name] = value

	# Optional, and signed when present - see OVERRIDE_FIELDS. Each is
	# pinned to this site by site_url() so a caller cannot turn the
	# receipt page into an open redirect or divert the back-office POST.
	optional = (
		("override_custom_receipt_page", customer.get("return_url"), "return_url"),
		("override_custom_cancel_page", customer.get("cancel_url"), "cancel_url"),
		("override_backoffice_post_url", customer.get("notify_url"), "notify_url"),
	)
	for field, value, label in optional:
		if value:
			fields[field] = _https_site_url(value, label)

	signed_field_names = list(SIGNED_FIELDS) + [f for f in OVERRIDE_FIELDS if f in fields]

	unsigned_field_names = list(UNSIGNED_FIELDS) + [
		f for f in OPTIONAL_BILLING_FIELDS if f in fields
	]

	fields["signed_field_names"] = ",".join(signed_field_names)
	fields["unsigned_field_names"] = ",".join(unsigned_field_names)
	fields["signature"] = _sign(_data_to_sign(fields, signed_field_names), _secret_key(settings))

	return {
		"method": "POST",
		"checkout_url": _checkout_url(settings),
		"fields": fields,
	}


def _signed_field_names(form_dict):
	"""The response's own signed_field_names, as a validated list."""
	raw = form_dict.get("signed_field_names")

	if not raw or not str(raw).strip():
		frappe.throw("Missing signed_field_names in the People's Bank response.")

	names = str(raw).split(",")

	if len(names) > MAX_SIGNED_FIELDS:
		frappe.throw("Malformed People's Bank response: signed_field_names lists too many fields.")

	if any(not name.strip() for name in names):
		frappe.throw("Malformed People's Bank response: signed_field_names has an empty entry.")

	return names


def verify_response(form_dict):
	signature = form_dict.get("signature")

	if not signature:
		frappe.throw("Missing signature in the People's Bank response.")

	names = _signed_field_names(form_dict)

	settings = _settings()

	expected = _sign(_data_to_sign(form_dict, names), _secret_key(settings))

	# compare_digest: this is the check that decides whether a payment is
	# genuine, so it must not leak how far a forgery matched.
	if not constant_time_equals(expected, str(signature)):
		frappe.throw("People's Bank response signature verification failed.")

	# Everything below reads this dict, never form_dict: CyberSource signs
	# every field it sends, so a field outside the signed set was added by
	# whoever posted to us and their guide says to ignore it.
	signed = {name: form_dict.get(name) for name in names}

	# Both are in CyberSource's signed set on every response, success or
	# failure. Missing means the payload is malformed - not a decline.
	for field in (DECISION_FIELD, REFERENCE_FIELD):
		if field not in signed:
			frappe.throw("Malformed People's Bank response: %s is not in the signed fields." % (field,))

	# Binds the response to our own merchant profile, the way PayHere's
	# merchant_id check does. Skipped when the field is absent from the
	# signed set entirely (some profiles omit it); never skipped merely
	# because the value is wrong.
	profile_id = signed.get("req_profile_id")
	if profile_id and not constant_time_equals(str(profile_id), _profile_id(settings)):
		frappe.throw("People's Bank response is for a different merchant profile.")

	decision = str(signed.get(DECISION_FIELD) or "").strip().upper()

	# auth_amount is what was actually authorised; req_amount is what we
	# asked for, and is all there is on a decline. Both are signed.
	amount = signed.get("auth_amount") or signed.get("req_amount")

	currency = str(signed.get("req_currency") or "").strip().upper()

	return {
		"order_id": signed.get(REFERENCE_FIELD),
		"status": STATUS_BY_DECISION.get(decision, "Failed"),
		"amount": str(amount) if amount not in (None, "") else None,
		"currency": currency or None,
		# True: the HMAC key is issued to our merchant profile alone, and
		# req_profile_id was checked against it above - so a response that
		# verifies is provably for our account, in the active mode.
		"merchant_verified": True,
		"raw": signed,
	}
