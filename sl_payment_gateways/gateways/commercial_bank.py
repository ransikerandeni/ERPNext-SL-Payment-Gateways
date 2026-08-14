"""Commercial Bank of Ceylon IPG - NOT YET IMPLEMENTED.

Same situation as People's Bank and Sampath Bank (see
gateways/peoples_bank.py): no public developer documentation found for
Commercial Bank's own IPG - integration details are issued to merchants
directly once onboarded. (Note: Commercial Bank is also one of the
partner banks for PayPal's Sri Lanka withdrawal support mentioned
elsewhere in this project's README - that's unrelated to this, a
merchant IPG for accepting card payments directly is a separate thing
from a bank being a PayPal withdrawal partner.)

Once you have a real spec, implement build_checkout() and
verify_response() here following the contract in gateways/base.py, add
a "Commercial Bank Settings" single doctype for the credentials they
issue, and register this module in api.py's GATEWAYS dict.
"""

import frappe


def build_checkout(order_id, amount, currency, customer):
	frappe.throw("Commercial Bank is not yet configured. See gateways/commercial_bank.py for what's needed.")


def verify_response(form_dict):
	frappe.throw("Commercial Bank is not yet configured. See gateways/commercial_bank.py for what's needed.")
