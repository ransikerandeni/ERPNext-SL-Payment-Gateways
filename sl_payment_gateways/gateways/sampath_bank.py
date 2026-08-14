"""Sampath Bank IPG - NOT YET IMPLEMENTED.

Same situation as People's Bank (see gateways/peoples_bank.py): no
public developer documentation found - Sampath Bank's IPG integration
spec is issued to merchants directly once onboarded (see
sampath.lk/digital-banking/online-banking, Internet Payment Gateway
section). Nothing implemented here without a real spec to work from.

Once you have it, implement build_checkout() and verify_response() here
following the contract in gateways/base.py, add a "Sampath Bank
Settings" single doctype for the credentials they issue, and register
this module in api.py's GATEWAYS dict.
"""

import frappe


def build_checkout(order_id, amount, currency, customer):
	frappe.throw("Sampath Bank is not yet configured. See gateways/sampath_bank.py for what's needed.")


def verify_response(form_dict):
	frappe.throw("Sampath Bank is not yet configured. See gateways/sampath_bank.py for what's needed.")
