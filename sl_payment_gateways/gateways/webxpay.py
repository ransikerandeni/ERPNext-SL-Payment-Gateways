"""WebXPay (webxpay.com) - Redirect Integration.

RSA-encrypts the payment string with WebXPay's public key for checkout,
and verifies an RSA-signed response on the way back. Settings (public
key, secret key, sandbox toggle) live in the "WebXPay Settings" single
doctype.

Padding: WebXPay's own sample PHP code (php-request.txt / php-response.txt
from their developer portal) calls openssl_public_encrypt() and
openssl_public_decrypt() with no padding argument, which defaults to
OPENSSL_PKCS1_PADDING (PKCS#1 v1.5) - block type 0x02 for encryption,
block type 0x01 for the signature-style public-decrypt. That's what's
implemented below.

Response format: WebXPay's Redirect Integration guide
(developers.webxpay.com/Guides/Redirect-Integration) documents the
decoded payment string as
order_id|order_reference_number|date_time_transaction|status_code|comment|payment_gateway_used
with status codes 0/00 = approved, 15 = declined. A real staging
transaction sends those six *plus two undocumented trailing amounts*
(requested, then captured), so both lengths are accepted - see
RESPONSE_FIELDS below.

Sandbox and live are separate WebXPay portals (stagingxpay.info and
webxpay.com) with separate merchant accounts and separate key pairs, so
"WebXPay Settings" holds both sets and `use_sandbox` picks one - see
docs/webxpay.md.

RESIDUAL RISKS you must handle in your own return handler
---------------------------------------------------------
1. The amount is undocumented and may be absent. When WebXPay sends the
   eight-field form, the captured figure is inside the signed blob and is
   returned as `amount` - compare it against your own expected price. On
   a six-field response `amount` is None and verify_response() proves only
   "WebXPay says order X reached status Y". Either way the currency is
   never sent, and their dashboard remains the source of truth for the
   figure; a caller must handle `amount is None` rather than assuming the
   check always runs.
2. The response carries no merchant identifier, and WebXPay signs with
   its own key, not a per-merchant one. A validly signed response for a
   given order_id is therefore not proof that *your* merchant account was
   credited. Only accept a response for an order you actually put into a
   pending state for WebXPay.
3. There is no nonce, so a response can be replayed. Make your handler
   idempotent and ignore responses for orders that are already settled.
"""

import base64
import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import frappe
from Crypto.PublicKey import RSA

from sl_payment_gateways.utils import (
	b64decode,
	clean_text,
	constant_time_equals,
	decode_utf8,
	format_amount,
	is_sandbox,
	mode_password,
	mode_value,
	validate_currency,
	validate_order_id,
)

# order_id | order_reference_number | date_time_transaction | status_code
# | comment | payment_gateway_used
#
# The six fields WebXPay's Redirect Integration guide §2.6 documents, and
# the six a response is *guaranteed* to open with. Confirmed against a
# real staging transaction:
#   SLT-ALCT-08-2026-00003|T109922026I20|2026-08-20 09:16:33|00|
#   00 - Approved|40|10.00|10.00
# - which also settles the field-order contradiction between their guide
# and the code comment in their php-response.txt sample (the guide is
# right; see README §6h).
RESPONSE_FIELDS = (
	"order_id",
	"order_reference_number",
	"date_time_transaction",
	"status_code",
	"comment",
	"payment_gateway_used",
)

# ...and the two undocumented trailing fields that same live response
# carried, matching the `requested_amount` / `transaction_amount` form
# parameters WebXPay posts alongside the blob. Absent from their guide
# entirely, so they are treated as optional: a response with either the
# documented six or these eight is accepted, and anything else is still
# rejected as malformed rather than being zipped into a half-empty dict.
#
# Only the copies inside the signed blob are ever read. The identically
# named POST parameters are unsigned and trivially forgeable.
OPTIONAL_RESPONSE_FIELDS = (
	"requested_amount",
	"transaction_amount",
)

ACCEPTED_FIELD_COUNTS = (
	len(RESPONSE_FIELDS),
	len(RESPONSE_FIELDS) + len(OPTIONAL_RESPONSE_FIELDS),
)

SUCCESS_STATUS_CODES = ("0", "00")

# Separate portals, separate merchant accounts, separate key pairs.
SANDBOX_CHECKOUT_URL = "https://stagingxpay.info/index.php?route=checkout/billing"
LIVE_CHECKOUT_URL = "https://webxpay.com/index.php?route=checkout/billing"

# PKCS#1 v1.5 requires at least 8 padding bytes. Enforcing it on the way
# back out rejects short-padding forgeries rather than trusting whatever
# structure the sender chose.
MIN_PADDING_LEN = 8

# Every request in WebXPay's own published samples (php-request.txt et
# al, developers.webxpay.com/Guides/Redirect-Integration) posts this
# exact literal value in a field labelled "Mechanism" - undocumented in
# the guide's own parameter table, but present on every sample form
# right next to secret_key and payment. It looks like a fixed protocol
# constant rather than a per-merchant secret (unlike secret_key, which
# their sample also hardcodes but obviously as a fake placeholder), so
# it's used as the default here. Overridable via *_enc_method in
# WebXPay Settings in case a given account turns out to need its own.
DEFAULT_ENC_METHOD = "JCs3J+6oSz4V0LgE0zi/Bg=="


def _settings():
	try:
		return frappe.get_doc("WebXPay Settings")
	except frappe.DoesNotExistError:
		frappe.throw("WebXPay Settings doctype does not exist - create it before using WebXPay.")


def _secret_key(settings):
	"""Read the merchant secret for the active mode. Kept out of
	_settings() so that verify_response() - which is reachable by guests -
	never has to touch the credential store at all."""
	return mode_password(settings, "secret_key", is_sandbox(settings))


def _public_key(settings):
	"""Staging and production are separate WebXPay portals issuing separate
	key pairs, so the mode decides which one verifies a response."""
	pem = mode_value(settings, "public_key", is_sandbox(settings))

	try:
		return RSA.import_key(pem)
	except (ValueError, IndexError, TypeError):
		frappe.throw(
			"WebXPay Settings holds an unreadable RSA public key for %s mode."
			% ("Sandbox" if is_sandbox(settings) else "Live",)
		)


def _enc_method(settings):
	"""The `enc_method` ("Mechanism") field - see DEFAULT_ENC_METHOD.
	Unlike public_key/secret_key this isn't a credential that must be
	filled in, so it never throws: empty settings just get the sample's
	default rather than an error naming a field most accounts won't need
	to touch."""
	prefix = "sandbox_" if is_sandbox(settings) else "live_"
	value = settings.get(prefix + "enc_method") or settings.get("enc_method")
	return value.strip() if value else DEFAULT_ENC_METHOD


def _i2osp(n, length):
	return n.to_bytes(length, "big")


def _os2ip(b):
	return int.from_bytes(b, "big")


def _rsa_encrypt_pkcs1_type2(plaintext: bytes, key: RSA.RsaKey) -> bytes:
	"""PKCS#1 v1.5 block type 0x02 (encryption) padding - equivalent to
	PHP's openssl_public_encrypt() with default padding."""
	n, e = key.n, key.e
	k = (n.bit_length() + 7) // 8

	if len(plaintext) > k - 11:
		frappe.throw("Plaintext too long for this RSA key size.")

	padding_len = k - len(plaintext) - 3
	ps = bytearray()
	while len(ps) < padding_len:
		# PS must be non-zero bytes; a zero would be read as the
		# terminator and truncate the message.
		ps += bytes(b for b in os.urandom(padding_len) if b != 0)
	ps = ps[:padding_len]

	em = b"\x00\x02" + bytes(ps) + b"\x00" + plaintext
	c = pow(_os2ip(em), e, n)
	return _i2osp(c, k)


def _rsa_public_decrypt_pkcs1_type1(ciphertext: bytes, key: RSA.RsaKey) -> bytes:
	"""PKCS#1 v1.5 block type 0x01 (signature) padding, reversed with the
	public key - equivalent to PHP's openssl_public_decrypt() verifying
	data produced by openssl_private_encrypt()."""
	n, e = key.n, key.e
	k = (n.bit_length() + 7) // 8

	if len(ciphertext) != k:
		frappe.throw("Invalid signature length for this RSA key.")

	c = _os2ip(ciphertext)
	if c >= n:
		frappe.throw("Ciphertext out of range for this RSA key.")

	em = _i2osp(pow(c, e, n), k)

	if em[0:2] != b"\x00\x01":
		frappe.throw("Invalid PKCS#1 signature padding (block type).")

	idx = 2
	while idx < len(em) and em[idx] == 0xFF:
		idx += 1

	if idx - 2 < MIN_PADDING_LEN:
		frappe.throw("Invalid PKCS#1 signature padding (too short).")

	if idx >= len(em) or em[idx] != 0x00:
		frappe.throw("Invalid PKCS#1 signature padding (terminator).")

	return em[idx + 1 :]


def build_checkout(order_id, amount, currency, customer):
	order_id = validate_order_id(order_id)
	amount = format_amount(amount)
	currency = validate_currency(currency)

	settings = _settings()
	key = _public_key(settings)
	secret_key = _secret_key(settings)

	plaintext = ("%s|%s" % (order_id, amount)).encode("utf-8")
	ciphertext = _rsa_encrypt_pkcs1_type2(plaintext, key)
	payment_field = base64.b64encode(ciphertext).decode("ascii")

	checkout_url = SANDBOX_CHECKOUT_URL if is_sandbox(settings) else LIVE_CHECKOUT_URL

	return {
		"method": "POST",
		"checkout_url": checkout_url,
		"fields": {
			# WebXPay requires an address; callers that don't collect one
			# can leave it out and get this placeholder.
			"address_line_one": clean_text(customer.get("address") or customer.get("organization"), 100, "N/A"),
			"first_name": clean_text(customer.get("first_name"), 30, "Customer"),
			"last_name": clean_text(customer.get("last_name"), 30, "-"),
			"email": clean_text(customer.get("email"), 100),
			"contact_number": clean_text(customer.get("contact_number"), 30),
			# WebXPay's integration posts this from the browser by design -
			# it identifies the merchant in the checkout form. It is still a
			# credential, which is why create_payment() is not reachable as a
			# public endpoint (see api.py).
			"secret_key": secret_key,
			"payment": payment_field,
			# See DEFAULT_ENC_METHOD - every WebXPay sample request posts
			# this field; without it, their server has failed to decrypt
			# `payment` correctly ("Invalid encryption") in practice.
			"enc_method": _enc_method(settings),
			"cms": "ERPNext",
			"process_currency": currency,
		},
	}


def _amount_or_none(value):
	"""Normalise a trailing amount field to a plain `123.45` string.

	Returns None rather than throwing when the field is absent or
	unparseable: a caller comparing against its own expected price treats
	None as "nothing to check here" (and, for WebXPay, falls back to the
	dashboard), which is the right outcome for an undocumented field that
	may simply not be there.
	"""
	if value is None or str(value).strip() == "":
		return None

	# Decimal directly rather than utils.format_amount(): that one reports
	# a bad value with frappe.throw(), which would both abort a response
	# that is otherwise perfectly valid and push a message into the
	# response for a field their guide never promised in the first place.
	try:
		parsed = Decimal(str(value).strip())
	except (InvalidOperation, ValueError):
		return None

	if not parsed.is_finite() or parsed < 0:
		return None

	return str(parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def verify_response(form_dict):
	payment = form_dict.get("payment")
	signature = form_dict.get("signature")

	if not payment or not signature:
		frappe.throw("Missing payment or signature.")

	settings = _settings()
	key = _public_key(settings)

	payment_plaintext = decode_utf8(b64decode(payment, "payment"), "payment")
	signature_plaintext = decode_utf8(
		_rsa_public_decrypt_pkcs1_type1(b64decode(signature, "signature"), key),
		"signature",
	)

	# compare_digest rather than != : this is the check that decides
	# whether a payment is genuine, so don't leak how far it matched.
	if not constant_time_equals(signature_plaintext, payment_plaintext):
		frappe.throw("WebXPay response signature does not match payment data.")

	parts = payment_plaintext.split("|")
	if len(parts) not in ACCEPTED_FIELD_COUNTS:
		# Previously a short payload zipped into a dict with missing keys
		# and fell through to status "Failed" - which reads like a declined
		# payment rather than the malformed response it actually is.
		frappe.throw(
			"Unexpected WebXPay response format: expected %s fields, got %d."
			% (" or ".join(str(n) for n in ACCEPTED_FIELD_COUNTS), len(parts))
		)

	parsed = dict(zip(RESPONSE_FIELDS + OPTIONAL_RESPONSE_FIELDS, parts, strict=False))

	status = "Paid" if parsed.get("status_code") in SUCCESS_STATUS_CODES else "Failed"

	return {
		"order_id": parsed.get("order_id"),
		"status": status,
		# The amount actually captured, when WebXPay sends it - it is inside
		# the signed blob, so it is as trustworthy as the status code next
		# to it. Still None on a six-field response, so a caller checking
		# `result["amount"]` sees None and knows it has to verify the figure
		# itself rather than finding no key and assuming there was nothing
		# to check. `requested_amount` (what we asked for) is in `raw` for
		# reconciliation; the captured figure is the one worth comparing.
		"amount": _amount_or_none(parsed.get("transaction_amount")),
		# Never sent, in either response length - the currency has to come
		# from the caller's own record of the order.
		"currency": None,
		# False: WebXPay signs with its own key, not a per-merchant one, and
		# the response carries no merchant id - so a valid signature does not
		# prove *our* account was credited. Callers must not settle an order
		# on this alone; see the residual risks above.
		"merchant_verified": False,
		"raw": parsed,
	}
