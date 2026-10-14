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

from sl_payment_gateways.gateways import (
	commercial_bank,
	payhere,
	peoples_bank_lkr,
	peoples_bank_usd,
	sampath_bank,
	webxpay,
)

GATEWAYS = {
	"WebXPay": webxpay,
	"PayHere": payhere,
	# People's Bank issues one CyberSource profile per currency, so it is two
	# gateways - see gateways/peoples_bank.py.
	"Peoples Bank (USD)": peoples_bank_usd,
	"Peoples Bank (LKR)": peoples_bank_lkr,
	"Sampath Bank": sampath_bank,
	"Commercial Bank": commercial_bank,
}

# Gateways with a real, working implementation - used by list_gateways()
# so the client script only offers ones that actually work.
IMPLEMENTED = ("WebXPay", "PayHere", "Peoples Bank (USD)", "Peoples Bank (LKR)")

# Names a gateway used to go by, still dispatched so a payment started before
# the rename settles: its return URL carries ?gateway=<old name>, fixed at the
# moment the checkout was signed. Never offered, never listed.
#
# "Peoples Bank" was the single USD profile before the bank split it per
# currency; its credentials moved to Peoples Bank USD Settings (see
# patches/v0_6/split_peoples_bank_gateway.py), so it verifies as USD.
LEGACY_NAMES = {
	"Peoples Bank": "Peoples Bank (USD)",
}

# Keys Frappe's own transport puts in form_dict. create_payment takes
# **customer, so without this they'd be forwarded to gateway modules as
# if they were customer details.
FRAMEWORK_KEYS = frozenset(
	{"cmd", "csrf_token", "data", "doctype", "name", "run_method", "_", "gateway", "order_id", "amount", "currency"}
)


def _get_gateway(name):
	module = GATEWAYS.get(LEGACY_NAMES.get(name, name))
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


def _is_enabled(name):
	"""Is `name` switched on? A module without an `is_enabled()` always is.

	People's Bank's gateways read an Enabled checkbox from their Settings. A
	failure to read it (an unmigrated site) reads as off and is logged: a
	gateway nobody can confirm is configured must not be offered to a payer.
	"""
	resolver = getattr(GATEWAYS[name], "is_enabled", None)

	if not resolver:
		return True

	try:
		return bool(resolver())
	except Exception:
		frappe.log_error(
			title="Could not read whether %s is enabled" % (name,),
			message=frappe.get_traceback(),
		)
		return False


def _offered_for(names, currency):
	"""`names`, less the gateways that should not be offered for a fee in `currency`.

	Gateways that share a `FAMILY` are alternatives to each other - People's
	Bank (USD) and People's Bank (LKR) are one bank's two profiles - and only
	the one that charges in the fee's own currency is offered: a USD fee shows
	People's Bank (USD), an LKR fee People's Bank (LKR), and nothing is
	converted.

	Where no enabled member of the family takes that currency (the LKR profile
	not switched on yet, say), the family's first enabled member is offered
	instead and the caller converts, which is what happened before the bank
	split its profiles. A gateway with no family is never filtered.
	"""
	currency = str(currency).strip().upper()

	offered = []

	for name in names:
		family = getattr(GATEWAYS[name], "FAMILY", None)

		if not family:
			offered.append(name)
			continue

		siblings = [n for n in names if getattr(GATEWAYS[n], "FAMILY", None) == family]
		matching = [n for n in siblings if currency in gateway_currencies(n)]

		if name in matching or (not matching and name == siblings[0]):
			offered.append(name)

	return offered


@frappe.whitelist()
def list_gateways(currency=None):
	"""Gateway names with a real implementation that are switched on, for the
	client script to render "Pay with X" options from without hardcoding the list.

	Pass `currency` - the currency the fee is priced in - to get only the
	gateways to offer for that fee (see _offered_for).
	"""
	names = [name for name in IMPLEMENTED if _is_enabled(name)]

	if currency:
		names = _offered_for(names, currency)

	return names


# What a gateway charges in when its module says nothing. WebXPay and PayHere
# collect LKR natively and have no second option, so the static default is the
# honest answer for them rather than a placeholder.
DEFAULT_CURRENCIES = ("LKR",)


def gateway_currencies(gateway):
	"""The currencies `gateway` will accept a checkout in, preferred first.

	A module may answer dynamically with `supported_currencies()`, or
	statically with a `CURRENCIES` tuple (People's Bank's two gateways do: each
	profile charges in exactly one currency), or not at all.

	A module that answers dynamically is reading Settings, and Settings can be
	missing on a site where the gateway was never configured. That must not
	take down a caller whose only question was "what can I offer?", so a
	failure falls back to the module's static answer and is logged.
	"""
	module = _get_gateway(gateway)

	resolver = getattr(module, "supported_currencies", None)

	if resolver:
		try:
			return list(resolver())
		except Exception:
			frappe.log_error(
				title="Could not read %s supported currencies" % (gateway,),
				message=frappe.get_traceback(),
			)

	return list(getattr(module, "CURRENCIES", DEFAULT_CURRENCIES))


@frappe.whitelist()
def list_gateway_currencies():
	"""{gateway name: [currency, ...]} for every implemented gateway.

	Whitelisted alongside list_gateways() and for the same reason: a front end
	should render the currencies a gateway actually accepts today, not a list
	hardcoded in a client script that nobody updates when Settings change.
	Reveals no credential and changes no state.

	Every implemented gateway, switched on or not, plus the legacy names: a
	return handler settling a payment started under an old name, or through a
	gateway switched off since, still needs to know what it charged in.
	"""
	names = list(IMPLEMENTED) + list(LEGACY_NAMES)

	return {name: gateway_currencies(name) for name in names}


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
