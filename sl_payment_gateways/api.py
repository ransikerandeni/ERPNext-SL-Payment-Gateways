"""Generic dispatcher for the pluggable payment gateway framework.

Adding a new gateway means writing one module in gateways/ implementing
build_checkout()/verify_response() (see gateways/base.py for the
contract), and adding one line to GATEWAYS below. Nothing else in this
app, and nothing in the Desk-pasted Server/Client Scripts, needs to
change - they call these two whitelisted functions generically with a
`gateway` name.

SECURITY NOTE - why create_payment refuses direct HTTP calls
------------------------------------------------------------
create_payment() signs a checkout for whatever `amount` its caller hands
it; that is the whole point of the function, and this app has no way to
know what any given order is *supposed* to cost - only the calling app
does. It also hands back gateway credentials that have to be posted from
the browser (WebXPay's secret_key).

Frappe only lets a Server Script reach a function through frappe.call()
if that function is @frappe.whitelist()'d, so the decorator can't simply
be dropped. Instead create_payment() asserts it is not itself the HTTP
entry point: it may only be reached *through* a caller's own whitelisted
endpoint, which is where order ownership, order state and the real price
are checked. Reaching /api/method/sl_payment_gateways.api.create_payment
straight from a browser or curl is refused.
"""

import frappe

from sl_payment_gateways.gateways import commercial_bank, payhere, peoples_bank, sampath_bank, webxpay

GATEWAYS = {
	"WebXPay": webxpay,
	"PayHere": payhere,
	"Peoples Bank": peoples_bank,
	"Sampath Bank": sampath_bank,
	"Commercial Bank": commercial_bank,
}

# Gateways with a real, working implementation - used by list_gateways()
# so the client script only offers ones that actually work.
IMPLEMENTED = ("WebXPay", "PayHere")

# Keys Frappe's own transport puts in form_dict. create_payment takes
# **customer, so without this they'd be forwarded to gateway modules as
# if they were customer details.
FRAMEWORK_KEYS = frozenset(
	{"cmd", "csrf_token", "data", "doctype", "name", "run_method", "_", "gateway", "order_id", "amount", "currency"}
)


def _get_gateway(name):
	module = GATEWAYS.get(name)
	if not module:
		# Escaped: frappe.throw renders its message as HTML in Desk, and
		# `name` is attacker-controlled.
		frappe.throw("Unknown payment gateway: %s" % (frappe.utils.escape_html(str(name)),))
	return module


def _assert_not_http_entry_point(method):
	"""Refuse to run when `method` is the endpoint the request called.

	Frappe sets form_dict["cmd"] to the dotted path being invoked for
	/api/method/<path> (and for the legacy ?cmd= route). A nested
	frappe.call() from a Server Script runs with the *outer* request's
	cmd still in place, so this distinguishes "called through someone
	else's authorised endpoint" from "called directly". Outside a request
	entirely (bench execute, background job, unit test) there is no cmd
	and nothing to refuse.
	"""
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	cmd = form_dict.get("cmd")

	if cmd and str(cmd).strip().strip("/") == method:
		_refuse(method)

	# Second, independent check on the request path. `cmd` is the reliable
	# signal today; this one costs nothing and means the guard doesn't
	# silently lapse if a future Frappe stops setting it. It deliberately
	# does not look at anything a nested call would inherit.
	request = getattr(frappe.local, "request", None)
	path = getattr(request, "path", None)

	if path and str(path).rstrip("/").endswith("/api/method/%s" % (method,)):
		_refuse(method)


def _refuse(method):
	raise frappe.PermissionError(
		"%s cannot be called directly over HTTP. Call it from your own "
		"whitelisted method, after you have checked who the caller is and "
		"determined the amount server-side." % (method,)
	)


@frappe.whitelist()
def list_gateways():
	"""Gateway names with a real implementation, for the client script to
	render "Pay with X" options from without hardcoding the list."""
	return list(IMPLEMENTED)


@frappe.whitelist()
def create_payment(gateway, order_id, amount, currency="LKR", **customer):
	"""Build a signed checkout. NOT a public endpoint - see the module docstring.

	`amount` is trusted as given, so the caller is responsible for
	deriving it server-side from its own records, never from the request.
	"""
	_assert_not_http_entry_point("sl_payment_gateways.api.create_payment")

	customer = {k: v for k, v in customer.items() if k not in FRAMEWORK_KEYS}

	return _get_gateway(gateway).build_checkout(order_id, amount, currency, customer)


def _keep_browser_session():
	"""Stop this request writing `sid=Guest` over the visitor's real session.

	A gateway return is a *cross-site* POST: the browser is handed a form
	on the gateway's domain and posts it to us, so SameSite=Lax withholds
	the session cookie and the request authenticates as Guest even when
	the same browser is signed in to Desk. Frappe then does what it does
	for any Guest request and sets `sid=Guest` on the way out - which
	lands in the browser that *is* signed in and silently logs it out.
	The participant pays, gets bounced to the login screen, and reads that
	as the payment having failed.

	Dropping the cookie from the outgoing response leaves whatever the
	browser already holds untouched: a signed-in session stays signed in,
	and a genuine guest simply doesn't get a new anonymous sid from this
	one endpoint. Nothing here depends on session state - the payload is
	verified cryptographically, not by who sent it.
	"""
	cookie_manager = getattr(frappe.local, "cookie_manager", None)
	cookies = getattr(cookie_manager, "cookies", None)

	# Best-effort: an internal detail of frappe.auth, so never let its
	# absence or a changed shape turn a verified payment into an error.
	if isinstance(cookies, dict):
		cookies.pop("sid", None)


@frappe.whitelist(allow_guest=True)
def payment_return(gateway):
	"""Verify an inbound gateway response. Safe to expose to guests: every
	gateway module cryptographically verifies the payload before returning
	anything, and this function itself changes no state.

	The caller is still responsible for the checks this app cannot make -
	that the order exists, is not already paid, was set up for this
	gateway, and (where the gateway echoes it) was paid the right amount.
	"""
	# Before verification, not after: a malformed or forged payload makes
	# verify_response() throw, and that response carries cookies too.
	_keep_browser_session()

	return _get_gateway(gateway).verify_response(frappe.form_dict)
