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
UX and aren't guaranteed to carry a verifiable payload. Point
notify_url at the shared gateway_payment_return Server Script;
return_url/cancel_url go straight back to the Slot Allocation form with
no verification step (the notify_url webhook already did the real work).
"""

import hashlib

import frappe


def _settings():
	settings = frappe.get_doc("PayHere Settings")
	if not settings.merchant_id or not settings.merchant_secret:
		frappe.throw("PayHere Settings is not configured.")
	return settings


def _secret_hash(merchant_secret: str) -> str:
	return hashlib.md5(merchant_secret.encode("utf-8")).hexdigest().upper()


def build_checkout(order_id, amount, currency, customer):
	settings = _settings()
	amount_str = "%.2f" % float(amount)

	checkout_hash = hashlib.md5(
		(
			"%s%s%s%s%s"
			% (settings.merchant_id, order_id, amount_str, currency, _secret_hash(settings.get_password("merchant_secret")))
		).encode("utf-8")
	).hexdigest().upper()

	checkout_url = (
		"https://sandbox.payhere.lk/pay/checkout" if settings.use_sandbox else "https://www.payhere.lk/pay/checkout"
	)

	return {
		"method": "POST",
		"checkout_url": checkout_url,
		"fields": {
			"merchant_id": settings.merchant_id,
			"return_url": frappe.utils.get_url("/app/slot-allocation/%s" % order_id),
			"cancel_url": frappe.utils.get_url("/app/slot-allocation/%s" % order_id),
			"notify_url": frappe.utils.get_url("/api/method/sl_payment_gateways.api.payment_return?gateway=PayHere"),
			"order_id": order_id,
			"items": "Slot Allocation registration fee (%s)" % (order_id,),
			"currency": currency,
			"amount": amount_str,
			"first_name": customer.get("first_name") or "Participant",
			"last_name": customer.get("last_name") or "-",
			"email": customer.get("email"),
			"phone": customer.get("contact_number"),
			# PayHere requires address/city/country but we don't collect a
			# real address from Participant - placeholders for now.
			"address": customer.get("organization") or "N/A",
			"city": "N/A",
			"country": customer.get("country") or "Sri Lanka",
			"hash": checkout_hash,
		},
	}


def verify_response(form_dict):
	merchant_id = form_dict.get("merchant_id")
	order_id = form_dict.get("order_id")
	payhere_amount = form_dict.get("payhere_amount")
	payhere_currency = form_dict.get("payhere_currency")
	status_code = form_dict.get("status_code")
	md5sig = form_dict.get("md5sig")

	if not all([merchant_id, order_id, payhere_amount, payhere_currency, status_code, md5sig]):
		frappe.throw("Missing required PayHere notification fields.")

	settings = _settings()

	if merchant_id != settings.merchant_id:
		frappe.throw("PayHere merchant_id mismatch.")

	expected = hashlib.md5(
		(
			"%s%s%s%s%s%s"
			% (
				merchant_id,
				order_id,
				payhere_amount,
				payhere_currency,
				status_code,
				_secret_hash(settings.get_password("merchant_secret")),
			)
		).encode("utf-8")
	).hexdigest().upper()

	if expected != md5sig:
		frappe.throw("PayHere md5sig verification failed.")

	if status_code == "2":
		status = "Paid"
	elif status_code == "0":
		status = "Pending"
	else:
		status = "Failed"

	return {
		"order_id": order_id,
		"status": status,
		"raw": dict(form_dict),
	}
