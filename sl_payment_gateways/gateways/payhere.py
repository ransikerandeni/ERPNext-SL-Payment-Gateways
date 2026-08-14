"""PayHere (payhere.lk) - Checkout API.

MD5-hash based (not RSA) - simpler than WebXPay, but still needs real
`hashlib`, which Server Scripts can't import either. Settings
(merchant_id, merchant_secret, sandbox toggle) live in the "PayHere
Settings" single doctype.

PayHere has two separate callbacks: `notify_url` is a genuine
server-to-server webhook (authoritative - carries the signed
merchant_id/order_id/payhere_amount/payhere_currency/status_code/md5sig
payload regardless of what the customer's browser does), while
`return_url`/`cancel_url` are just where the browser gets redirected for
UX and aren't guaranteed to carry a verifiable payload.

All three URLs, plus the `items` description, are supplied by the caller
(this app has no idea what your orders are called or where your handler
lives). `notify_url` is mandatory and must point at *your* whitelisted,
guest-allowed return handler - the one that updates your order - not at
sl_payment_gateways.api.payment_return, which verifies the payload and
returns it but deliberately changes no state.

MD5 is PayHere's choice, not ours; the signature scheme is fixed by their
API and cannot be strengthened from this side.

RESIDUAL RISK: PayHere echoes payhere_amount/payhere_currency inside the
signed payload, and verify_response() returns them as `amount`/`currency`.
Compare them against what the order should cost before you mark anything
paid - a valid signature only proves PayHere sent the message, not that
the customer paid the right price.
"""

import hashlib

import frappe

from sl_payment_gateways.utils import (
	clean_text,
	constant_time_equals,
	format_amount,
	site_url,
	validate_currency,
	validate_order_id,
)

# PayHere's API documents order_id as up to 50 characters.
MAX_ORDER_ID_LENGTH = 50

STATUS_BY_CODE = {
	"2": "Paid",
	"0": "Pending",
	"-1": "Failed",  # cancelled
	"-2": "Failed",  # failed
	"-3": "Failed",  # charged back
}

REQUIRED_RESPONSE_FIELDS = (
	"merchant_id",
	"order_id",
	"payhere_amount",
	"payhere_currency",
	"status_code",
	"md5sig",
)


def _settings():
	try:
		settings = frappe.get_doc("PayHere Settings")
	except frappe.DoesNotExistError:
		frappe.throw("PayHere Settings doctype does not exist - create it before using PayHere.")

	if not settings.merchant_id:
		frappe.throw("PayHere Settings is not configured (no Merchant ID).")

	return settings


def _merchant_secret(settings):
	secret = settings.get_password("merchant_secret", raise_exception=False)

	if not secret:
		frappe.throw("PayHere Settings is not configured (no Merchant Secret).")

	return secret


# MD5 below is not a choice: PayHere's Checkout API defines its hash and
# md5sig this way, so anything stronger simply would not verify.
def _secret_hash(merchant_secret: str) -> str:
	return hashlib.md5(merchant_secret.encode("utf-8")).hexdigest().upper()  # noqa: S324


def _md5_upper(value: str) -> str:
	return hashlib.md5(value.encode("utf-8")).hexdigest().upper()  # noqa: S324


def build_checkout(order_id, amount, currency, customer):
	order_id = validate_order_id(order_id, max_length=MAX_ORDER_ID_LENGTH)
	amount_str = format_amount(amount)
	currency = validate_currency(currency)

	settings = _settings()
	merchant_id = str(settings.merchant_id)

	# Where PayHere's authoritative webhook goes. No default: silently
	# falling back to this app's own payment_return would verify the
	# payload and then discard it, leaving every payment unrecorded.
	notify_url = site_url(customer.get("notify_url"), "notify_url")

	# Browser redirects. These are cosmetic, but still pinned to this site
	# so a caller can't turn a checkout into an open redirect.
	return_url = site_url(customer.get("return_url") or "/", "return_url")
	cancel_url = site_url(customer.get("cancel_url") or "/", "cancel_url")

	checkout_hash = _md5_upper(
		"%s%s%s%s%s"
		% (merchant_id, order_id, amount_str, currency, _secret_hash(_merchant_secret(settings)))
	)

	checkout_url = (
		"https://sandbox.payhere.lk/pay/checkout" if settings.use_sandbox else "https://www.payhere.lk/pay/checkout"
	)

	return {
		"method": "POST",
		"checkout_url": checkout_url,
		"fields": {
			"merchant_id": merchant_id,
			"return_url": return_url,
			"cancel_url": cancel_url,
			"notify_url": notify_url,
			"order_id": order_id,
			"items": clean_text(customer.get("items"), 100, "Order %s" % (order_id,)),
			"currency": currency,
			"amount": amount_str,
			"first_name": clean_text(customer.get("first_name"), 30, "Customer"),
			"last_name": clean_text(customer.get("last_name"), 30, "-"),
			"email": clean_text(customer.get("email"), 100),
			"phone": clean_text(customer.get("contact_number"), 30),
			# PayHere requires address/city/country; callers that don't
			# collect a real address get these placeholders.
			"address": clean_text(customer.get("address") or customer.get("organization"), 100, "N/A"),
			"city": clean_text(customer.get("city"), 50, "N/A"),
			"country": clean_text(customer.get("country"), 50, "Sri Lanka"),
			"hash": checkout_hash,
		},
	}


def verify_response(form_dict):
	values = {field: form_dict.get(field) for field in REQUIRED_RESPONSE_FIELDS}

	missing = [field for field, value in values.items() if value in (None, "")]
	if missing:
		frappe.throw("Missing required PayHere notification fields: %s." % (", ".join(missing),))

	# The signature is computed over exactly the bytes PayHere sent, so
	# hash the raw values and only normalise copies used for decisions.
	merchant_id = str(values["merchant_id"])
	order_id = str(values["order_id"])
	payhere_amount = str(values["payhere_amount"])
	payhere_currency = str(values["payhere_currency"])
	status_code = str(values["status_code"])
	md5sig = str(values["md5sig"])

	settings = _settings()

	if not constant_time_equals(merchant_id, settings.merchant_id):
		frappe.throw("PayHere merchant_id mismatch.")

	expected = _md5_upper(
		"%s%s%s%s%s%s"
		% (
			merchant_id,
			order_id,
			payhere_amount,
			payhere_currency,
			status_code,
			_secret_hash(_merchant_secret(settings)),
		)
	)

	# compare_digest: md5sig is a secret-keyed MAC, so the comparison
	# itself must not reveal how many leading characters were right.
	if not constant_time_equals(expected, md5sig.strip().upper()):
		frappe.throw("PayHere md5sig verification failed.")

	return {
		"order_id": order_id,
		"status": STATUS_BY_CODE.get(status_code.strip(), "Failed"),
		# Signed by PayHere, so these are trustworthy as "what PayHere
		# recorded" - the caller still has to check them against the order.
		"amount": payhere_amount,
		"currency": payhere_currency,
		"raw": dict(form_dict),
	}
