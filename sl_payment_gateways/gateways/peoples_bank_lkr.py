"""People's Bank (LKR) - the CyberSource profile People's Bank issued for LKR.

The protocol lives in peoples_bank.py; this module only binds it to the LKR
profile and its Settings ("Peoples Bank LKR Settings"), so api.GATEWAYS can
dispatch to it like any other gateway module.
"""

from sl_payment_gateways.gateways import peoples_bank

PROFILE = peoples_bank.LKR
FAMILY = peoples_bank.FAMILY

# What this profile charges in - fixed by the bank, not by a setting.
CURRENCIES = (PROFILE.currency,)


def is_enabled():
	return peoples_bank.is_enabled(PROFILE)


def build_checkout(order_id, amount, currency, customer):
	return peoples_bank.build_checkout(PROFILE, order_id, amount, currency, customer)


def verify_response(form_dict):
	return peoples_bank.verify_response(PROFILE, form_dict)
