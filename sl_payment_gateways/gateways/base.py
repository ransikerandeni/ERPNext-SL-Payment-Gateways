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

	`customer` carries whatever the caller passed through. Conventional
	keys, all optional unless a gateway says otherwise:

		first_name, last_name, email, contact_number,
		address, city, country, organization,
		items,                       # order description shown at checkout
		return_url, cancel_url,      # where the browser goes afterwards
		notify_url                   # server-to-server webhook target

	A gateway module uses only the keys it needs, via .get(). Any URL a
	caller supplies must be run through utils.site_url() so it can't point
	off-site. Validate order_id/amount/currency through the helpers in
	sl_payment_gateways.utils rather than trusting them: they arrive from
	an HTTP request and end up inside signed, delimited payment strings.

	IMPORTANT: `amount` is whatever the caller said. This app cannot know
	what an order should cost, so build_checkout() will faithfully sign a
	checkout for any figure. The caller must derive it server-side from
	its own records - see the security note in api.py.

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
			"amount": "1500.00" | None,   # None if the gateway omits it
			"currency": "LKR" | None,
			"merchant_verified": True | False,
			"raw": {...},         # parsed gateway response, for logging
		}

	`merchant_verified` is a property of the *protocol*, not of this
	particular payload: True means a successful verification proves the
	payment was made to our own merchant account, because the signature
	is keyed on a secret only we hold (PayHere's md5sig). False means it
	does not - the gateway signs with a key shared across merchants and
	sends no merchant identifier, so any of its merchants could produce a
	validly signed response for an arbitrary order id (WebXPay).

	Callers use it to decide how much the signature alone is worth. With
	False, a verified response must be corroborated against local state -
	e.g. that this order was actually put into a pending state for this
	gateway - before any money is considered received.

	What a successful return does NOT establish, and what every caller
	therefore still has to check for itself:

	  * that the order exists, is unsettled, and was set up for this
	    gateway (nothing here consults your records);
	  * that the amount is right - compare `amount`/`currency` against the
	    order, and note that a gateway reporting None means the response
	    carries no amount to check at all;
	  * that this is not a replay - none of these gateways send a nonce,
	    so make your handler idempotent.
	"""
	raise NotImplementedError
