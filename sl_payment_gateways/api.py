"""Generic dispatcher for the pluggable payment gateway framework.

Adding a new gateway means writing one module in gateways/ implementing
build_checkout()/verify_response() (see gateways/base.py for the
contract), and adding one line to GATEWAYS below. Nothing else in this
app, and nothing in the Desk-pasted Server/Client Scripts, needs to
change - they call these two whitelisted functions generically with a
`gateway` name.
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


def _get_gateway(name):
	module = GATEWAYS.get(name)
	if not module:
		frappe.throw("Unknown payment gateway: %s" % (name,))
	return module


@frappe.whitelist()
def list_gateways():
	"""Gateway names with a real implementation, for the client script to
	render "Pay with X" options from without hardcoding the list."""
	return list(IMPLEMENTED)


@frappe.whitelist()
def create_payment(gateway, order_id, amount, currency="LKR", **customer):
	return _get_gateway(gateway).build_checkout(order_id, amount, currency, customer)


@frappe.whitelist(allow_guest=True)
def payment_return(gateway):
	return _get_gateway(gateway).verify_response(frappe.form_dict)
