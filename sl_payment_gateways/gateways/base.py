# The gateway module contract.
#
# Every module in this package (webxpay.py, payhere.py, ...) implements
# these two functions. There's no base class to inherit from - the
# contract is just these two function signatures, kept consistent so
# api.py can dispatch to any of them the same way, and so a new gateway
# is "write one module, register it in api.GATEWAYS" with nothing else
# to change. This file is documentation only, not imported anywhere.


def build_checkout(order_id: str, amount: str, currency: str, customer: dict) -> dict:
	"""Build whatever the gateway needs to start a checkout.

	`customer` has whatever fields the caller passed through (e.g.
	first_name, last_name, email, contact_number) - a gateway module
	only needs to use the ones it actually requires and should ignore
	the rest via .get(), since not every gateway needs every field.

	Returns a dict the client script can act on directly:
		{
			"method": "POST" | "GET",
			"checkout_url": "...",
			"fields": {...},   # form fields (POST) or query params (GET)
		}
	"""
	raise NotImplementedError


def verify_response(form_dict) -> dict:
	"""Verify an inbound response from the gateway and parse it.

	`form_dict` is frappe.form_dict from the request the gateway sent
	the browser to (or posted to). Raise (frappe.throw or any
	exception) if verification fails or the payload is malformed -
	callers must treat any exception as "not verified, do not trust
	this". On success, return:
		{
			"order_id": "...",   # our own reference (Slot Allocation name)
			"status": "Paid" | "Failed" | "Pending",
			"raw": {...},         # parsed gateway response, for logging
		}
	"""
	raise NotImplementedError
