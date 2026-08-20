"""The Settings doctypes' ERPNext integration hooks.

These matter to the *caller's* accounting records, not to the checkout:
linking a Payment Gateway Account to a Payment Request is what makes a
WebXPay/PayHere payment name its gateway the way a PayPal one does, and
these hooks are what stop core reacting to that link by trying to drive
the checkout itself.
"""

import pytest

from sl_payment_gateways.sl_payment_gateways.doctype.payhere_settings.payhere_settings import (
	PayHereSettings,
)
from sl_payment_gateways.sl_payment_gateways.doctype.webxpay_settings.webxpay_settings import (
	WebXPaySettings,
)

SETTINGS_CLASSES = (WebXPaySettings, PayHereSettings)


@pytest.mark.parametrize("settings_class", SETTINGS_CLASSES)
class TestPaymentRequestSubmissionHook:
	def test_opts_out_of_cores_own_checkout_flow(self, settings_class):
		# ERPNext reads this as its `send_mail` flag: anything truthy and
		# Payment Request.on_submit() calls set_payment_request_url(),
		# which asks this controller for a get_payment_url() that does not
		# and cannot exist - these gateways are signed POST forms, not GET
		# redirects. Returning False is the documented opt-out.
		assert settings_class(name="Settings").on_payment_request_submission(object()) is False

	def test_hook_is_named_exactly_what_erpnext_looks_for(self, settings_class):
		# Found by hasattr() on the controller, so a rename is a silent
		# regression: core would fall back to returning True and start
		# building payment URLs again.
		assert hasattr(settings_class, "on_payment_request_submission")

	def test_hook_ignores_the_request_it_is_handed(self, settings_class):
		# It must not depend on anything about the Payment Request - core
		# passes the doc, and this app has no business inspecting it.
		settings = settings_class(name="Settings")

		assert settings.on_payment_request_submission(None) is False
		assert settings.on_payment_request_submission({"grand_total": 10}) is False
