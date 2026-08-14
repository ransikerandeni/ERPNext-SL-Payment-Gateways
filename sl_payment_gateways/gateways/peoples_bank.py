"""People's Bank (peoplesbank.lk) - NOT YET IMPLEMENTED.

Unlike WebXPay and PayHere, People's Bank's IPG has no public developer
documentation - their technical integration spec is only shared with
merchants after completing an in-branch application and signing their
IPG Merchant Agreement (see peoplesbank.lk/merchant-services). There is
nothing to safely implement here yet without guessing at a real bank's
signature/encryption scheme, which is exactly the kind of mistake that
causes silent payment failures.

Once you have the real integration spec from People's Bank, implement
build_checkout() and verify_response() here following the same contract
as webxpay.py / payhere.py (see gateways/base.py), add a "People's Bank
Settings" single doctype for whatever credentials they issue, and
register this module in api.py's GATEWAYS dict - nothing else in this
project needs to change.
"""

import frappe


def build_checkout(order_id, amount, currency, customer):
	frappe.throw("People's Bank is not yet configured. See gateways/peoples_bank.py for what's needed.")


def verify_response(form_dict):
	frappe.throw("People's Bank is not yet configured. See gateways/peoples_bank.py for what's needed.")
