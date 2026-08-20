import types

import pytest

import frappe
from sl_payment_gateways import api

CREATE_PAYMENT_CMD = "sl_payment_gateways.api.create_payment"
NOTIFY = "/api/method/gateway_payment_return?gateway=PayHere"


class TestGatewayDispatch:
	def test_lists_only_working_gateways(self):
		assert api.list_gateways() == ["WebXPay", "PayHere"]

	def test_scaffolded_gateways_are_registered_but_not_offered(self):
		for name in ("Peoples Bank", "Sampath Bank", "Commercial Bank"):
			assert name in api.GATEWAYS
			assert name not in api.list_gateways()

	def test_every_registered_gateway_implements_the_contract(self):
		for name, module in api.GATEWAYS.items():
			assert callable(getattr(module, "build_checkout", None)), name
			assert callable(getattr(module, "verify_response", None)), name

	def test_unimplemented_gateways_refuse_rather_than_half_work(self):
		for name in ("Peoples Bank", "Sampath Bank", "Commercial Bank"):
			with pytest.raises(frappe.ValidationError, match="not yet configured"):
				api.GATEWAYS[name].build_checkout("SO-1", "1.00", "LKR", {})
			with pytest.raises(frappe.ValidationError, match="not yet configured"):
				api.GATEWAYS[name].verify_response(frappe._dict({}))

	def test_verify_response_returns_the_full_contract(
		self, payhere_settings, payhere_notification, webxpay_settings, sign_webxpay
	):
		# Every implemented gateway must return the same key set, so a
		# caller can read result["amount"] / result["merchant_verified"]
		# without knowing which gateway answered.
		expected_keys = {"order_id", "status", "amount", "currency", "merchant_verified", "raw"}

		frappe.local.form_dict = payhere_notification()
		assert set(api.payment_return("PayHere")) == expected_keys

		frappe.local.form_dict = frappe._dict(
			sign_webxpay("SO-0001|REF|2026-08-14|00|Approved|VISA")
		)
		assert set(api.payment_return("WebXPay")) == expected_keys

	def test_merchant_verified_reflects_the_protocol(
		self, payhere_settings, payhere_notification, webxpay_settings, sign_webxpay
	):
		# PayHere's md5sig is keyed on our own secret; WebXPay's signature
		# is not. Callers rely on this to decide how much a valid
		# signature is worth on its own.
		frappe.local.form_dict = payhere_notification()
		assert api.payment_return("PayHere")["merchant_verified"] is True

		frappe.local.form_dict = frappe._dict(
			sign_webxpay("SO-0001|REF|2026-08-14|00|Approved|VISA")
		)
		assert api.payment_return("WebXPay")["merchant_verified"] is False

	def test_unknown_gateway_throws(self):
		with pytest.raises(frappe.ValidationError, match="Unknown payment gateway"):
			api._get_gateway("Nonexistent Bank")

	def test_unknown_gateway_name_is_html_escaped(self):
		# frappe.throw renders as HTML in Desk, and the name came from the
		# request.
		with pytest.raises(frappe.ValidationError) as excinfo:
			api._get_gateway("<img src=x onerror=alert(1)>")

		assert "<img" not in str(excinfo.value)
		assert "&lt;img" in str(excinfo.value)


class TestCreatePaymentIsNotAPublicEndpoint:
	"""The highest-impact fix in this app.

	create_payment() signs a checkout for whatever amount it is handed and
	returns WebXPay's merchant secret_key in the form fields. Left
	reachable at /api/method/..., any logged-in user could price their own
	order at 1.00 and read the credential back out of the response.
	"""

	def test_refuses_when_it_is_the_http_entry_point(self, payhere_settings):
		frappe.local.form_dict = frappe._dict({"cmd": CREATE_PAYMENT_CMD})

		with pytest.raises(frappe.PermissionError, match="cannot be called directly"):
			api.create_payment("PayHere", "SO-1", "1.00", "LKR", notify_url=NOTIFY)

	def test_refuses_regardless_of_slash_padding(self, payhere_settings):
		frappe.local.form_dict = frappe._dict({"cmd": "/%s/" % CREATE_PAYMENT_CMD})

		with pytest.raises(frappe.PermissionError):
			api.create_payment("PayHere", "SO-1", "1.00", "LKR", notify_url=NOTIFY)

	def test_refuses_on_request_path_even_without_cmd(self, payhere_settings):
		# Backstop for the day form_dict["cmd"] stops being set.
		import types

		frappe.local.form_dict = frappe._dict()
		frappe.local.request = types.SimpleNamespace(
			path="/api/method/%s" % CREATE_PAYMENT_CMD
		)
		try:
			with pytest.raises(frappe.PermissionError):
				api.create_payment("PayHere", "SO-1", "1.00", "LKR", notify_url=NOTIFY)
		finally:
			frappe.local.request = None

	def test_request_path_for_another_endpoint_is_allowed(self, payhere_settings):
		import types

		frappe.local.form_dict = frappe._dict({"cmd": "create_gateway_payment"})
		frappe.local.request = types.SimpleNamespace(path="/api/method/create_gateway_payment")
		try:
			assert api.create_payment("PayHere", "SO-1", "1.00", "LKR", notify_url=NOTIFY)["method"] == "POST"
		finally:
			frappe.local.request = None

	def test_allows_a_nested_call_from_someone_elses_endpoint(self, payhere_settings):
		# A Server Script's frappe.call() runs with the outer request's cmd
		# still set - that is the authorised path.
		frappe.local.form_dict = frappe._dict({"cmd": "create_gateway_payment"})

		result = api.create_payment("PayHere", "SO-1", "1.00", "LKR", notify_url=NOTIFY)

		assert result["method"] == "POST"

	def test_allows_calls_outside_a_request(self, payhere_settings):
		# bench execute, background jobs, unit tests: no cmd, nothing to refuse.
		frappe.local.form_dict = frappe._dict()

		assert api.create_payment("PayHere", "SO-1", "1.00", "LKR", notify_url=NOTIFY)["method"] == "POST"

	def test_the_guard_is_wired_into_the_real_function(self):
		# Guard against someone deleting the call while tests still pass by
		# exercising the helper directly.
		import inspect

		assert "_assert_not_http_entry_point" in inspect.getsource(api.create_payment)

	def test_payment_return_stays_publicly_reachable(self):
		# Unlike create_payment, this one is meant to be hit by gateways
		# with no session, and is safe because it verifies before returning.
		assert getattr(api.payment_return, "allow_guest", False) is True
		assert getattr(api.create_payment, "allow_guest", False) is False


class TestFrameworkKeysAreStripped:
	def test_transport_keys_do_not_reach_the_gateway(self, payhere_settings, monkeypatch):
		seen = {}

		def fake_build(order_id, amount, currency, customer):
			seen.update(customer)
			return {}

		monkeypatch.setattr(api.GATEWAYS["PayHere"], "build_checkout", fake_build)

		api.create_payment(
			"PayHere",
			"SO-1",
			"1.00",
			"LKR",
			csrf_token="abc",
			cmd="something",
			doctype="User",
			first_name="Ransike",
		)

		assert seen == {"first_name": "Ransike"}


class TestPaymentReturn:
	def test_dispatches_to_the_named_gateway(self, payhere_settings, payhere_notification):
		frappe.local.form_dict = payhere_notification()

		result = api.payment_return("PayHere")

		assert result["order_id"] == "SO-0001"
		assert result["status"] == "Paid"

	def test_unknown_gateway_throws(self):
		with pytest.raises(frappe.ValidationError, match="Unknown payment gateway"):
			api.payment_return("Nonexistent Bank")

	def test_does_not_send_a_session_cookie_back(self, payhere_settings, payhere_notification):
		# A gateway return is a cross-site POST, so it authenticates as
		# Guest even from a signed-in browser. Letting Frappe set
		# `sid=Guest` on the response logs that browser out mid-payment.
		frappe.local.form_dict = payhere_notification()
		frappe.local.cookie_manager = types.SimpleNamespace(cookies={"sid": "Guest", "country": "LK"})

		api.payment_return("PayHere")

		assert "sid" not in frappe.local.cookie_manager.cookies
		# Only the session cookie - nothing else on the response is ours
		# to drop.
		assert frappe.local.cookie_manager.cookies["country"] == "LK"

	def test_session_cookie_is_dropped_even_when_verification_fails(
		self, payhere_settings, payhere_notification
	):
		# The error response carries cookies too, so a forged or malformed
		# payload must not be able to log a signed-in browser out either.
		payload = payhere_notification()
		payload["md5sig"] = "0" * 32
		frappe.local.form_dict = payload
		frappe.local.cookie_manager = types.SimpleNamespace(cookies={"sid": "Guest"})

		with pytest.raises(frappe.ValidationError):
			api.payment_return("PayHere")

		assert "sid" not in frappe.local.cookie_manager.cookies

	@pytest.mark.parametrize(
		"cookie_manager",
		[None, types.SimpleNamespace(), types.SimpleNamespace(cookies=None)],
	)
	def test_survives_a_frappe_without_the_cookie_internals(
		self, payhere_settings, payhere_notification, cookie_manager
	):
		# frappe.auth's cookie plumbing is an internal detail: if it moves,
		# a verified payment must still settle rather than erroring.
		frappe.local.form_dict = payhere_notification()
		frappe.local.cookie_manager = cookie_manager

		assert api.payment_return("PayHere")["status"] == "Paid"

	def test_unverifiable_payload_raises_rather_than_returning_a_status(
		self, payhere_settings, payhere_notification
	):
		payload = payhere_notification()
		payload["md5sig"] = "0" * 32
		frappe.local.form_dict = payload

		with pytest.raises(frappe.ValidationError):
			api.payment_return("PayHere")
