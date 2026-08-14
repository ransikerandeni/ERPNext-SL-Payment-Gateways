import hashlib

import pytest

import frappe
from sl_payment_gateways.gateways import payhere

from .conftest import PAYHERE_MERCHANT_ID, PAYHERE_SECRET

NOTIFY = "/api/method/gateway_payment_return?gateway=PayHere"


def reference_checkout_hash(merchant_id, order_id, amount, currency, secret):
	"""PayHere's documented formula, spelled out independently of the
	implementation so the test would catch a change to either."""
	secret_hash = hashlib.md5(secret.encode()).hexdigest().upper()
	joined = "%s%s%s%s%s" % (merchant_id, order_id, amount, currency, secret_hash)
	return hashlib.md5(joined.encode()).hexdigest().upper()


class TestBuildCheckout:
	def test_hash_matches_payheres_documented_formula(self, payhere_settings):
		fields = payhere.build_checkout(
			"SO-0001", "1500.00", "LKR", {"notify_url": NOTIFY}
		)["fields"]

		assert fields["hash"] == reference_checkout_hash(
			PAYHERE_MERCHANT_ID, "SO-0001", "1500.00", "LKR", PAYHERE_SECRET
		)

	def test_amount_is_normalised_and_hashed_consistently(self, payhere_settings):
		fields = payhere.build_checkout("SO-1", 1500.5, "LKR", {"notify_url": NOTIFY})["fields"]

		assert fields["amount"] == "1500.50"
		assert fields["hash"] == reference_checkout_hash(
			PAYHERE_MERCHANT_ID, "SO-1", "1500.50", "LKR", PAYHERE_SECRET
		)

	def test_notify_url_is_required(self, payhere_settings):
		# Defaulting this silently is what made PayHere payments verify and
		# then never reach any order record.
		with pytest.raises(frappe.ValidationError, match="notify_url is required"):
			payhere.build_checkout("SO-1", "1.00", "LKR", {})

	@pytest.mark.parametrize(
		"url",
		["https://evil.example.net/steal", "//evil.example.net/steal", "javascript:alert(1)"],
	)
	def test_off_site_urls_rejected(self, payhere_settings, url):
		with pytest.raises(frappe.ValidationError):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": url})

		with pytest.raises(frappe.ValidationError):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY, "return_url": url})

	def test_urls_resolve_against_the_site(self, payhere_settings):
		fields = payhere.build_checkout(
			"SO-1", "1.00", "LKR", {"notify_url": NOTIFY, "return_url": "/app/order/SO-1"}
		)["fields"]

		assert fields["notify_url"] == "https://erp.example.com" + NOTIFY
		assert fields["return_url"] == "https://erp.example.com/app/order/SO-1"
		assert fields["cancel_url"] == "https://erp.example.com/"

	def test_items_defaults_to_the_order_not_a_hardcoded_product(self, payhere_settings):
		fields = payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})["fields"]

		assert fields["items"] == "Order SO-1"

		custom = payhere.build_checkout(
			"SO-1", "1.00", "LKR", {"notify_url": NOTIFY, "items": "Conference registration"}
		)["fields"]
		assert custom["items"] == "Conference registration"

	def test_order_id_length_limited_to_payheres_maximum(self, payhere_settings):
		with pytest.raises(frappe.ValidationError):
			payhere.build_checkout("S" * 51, "1.00", "LKR", {"notify_url": NOTIFY})

	def test_sandbox_and_live_urls(self, payhere_settings):
		assert "sandbox.payhere.lk" in payhere.build_checkout(
			"SO-1", "1.00", "LKR", {"notify_url": NOTIFY}
		)["checkout_url"]

		payhere_settings["use_sandbox"] = 0
		assert "www.payhere.lk" in payhere.build_checkout(
			"SO-1", "1.00", "LKR", {"notify_url": NOTIFY}
		)["checkout_url"]

	def test_unconfigured_settings_throw(self, payhere_settings):
		payhere_settings["merchant_id"] = None
		with pytest.raises(frappe.ValidationError, match="not configured"):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})

	def test_missing_secret_throws(self, payhere_settings):
		object.__setattr__(payhere_settings, "_passwords", {})
		with pytest.raises(frappe.ValidationError, match="Merchant Secret"):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})

	def test_missing_doctype_throws_clearly(self):
		with pytest.raises(frappe.ValidationError, match="does not exist"):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})


class TestVerifyResponse:
	def test_accepts_a_genuine_notification(self, payhere_settings, payhere_notification):
		result = payhere.verify_response(payhere_notification())

		assert result == {
			"order_id": "SO-0001",
			"status": "Paid",
			"amount": "1500.00",
			"currency": "LKR",
			"raw": dict(payhere_notification()),
		}

	@pytest.mark.parametrize(
		("code", "status"),
		[("2", "Paid"), ("0", "Pending"), ("-1", "Failed"), ("-2", "Failed"), ("-3", "Failed")],
	)
	def test_status_code_mapping(self, payhere_settings, payhere_notification, code, status):
		assert payhere.verify_response(payhere_notification(status_code=code))["status"] == status

	def test_unknown_status_code_is_not_paid(self, payhere_settings, payhere_notification):
		assert payhere.verify_response(payhere_notification(status_code="7"))["status"] == "Failed"

	def test_rejects_forged_success(self, payhere_settings, payhere_notification):
		# Take a genuine "failed" notification and flip it to paid.
		payload = payhere_notification(status_code="-2")
		payload["status_code"] = "2"

		with pytest.raises(frappe.ValidationError, match="md5sig verification failed"):
			payhere.verify_response(payload)

	def test_rejects_tampered_amount(self, payhere_settings, payhere_notification):
		payload = payhere_notification(amount="1500.00")
		payload["payhere_amount"] = "1.00"

		with pytest.raises(frappe.ValidationError, match="md5sig verification failed"):
			payhere.verify_response(payload)

	def test_rejects_tampered_order_id(self, payhere_settings, payhere_notification):
		payload = payhere_notification(order_id="SO-0001")
		payload["order_id"] = "SO-9999"

		with pytest.raises(frappe.ValidationError, match="md5sig verification failed"):
			payhere.verify_response(payload)

	def test_rejects_signature_from_another_merchant_secret(self, payhere_settings, payhere_notification):
		payload = payhere_notification()
		object.__setattr__(payhere_settings, "_passwords", {"merchant_secret": "some-other-secret"})

		with pytest.raises(frappe.ValidationError, match="md5sig verification failed"):
			payhere.verify_response(payload)

	def test_rejects_another_merchants_notification(self, payhere_settings, payhere_notification):
		# Correctly signed for merchant 999999, replayed at our endpoint.
		with pytest.raises(frappe.ValidationError, match="merchant_id mismatch"):
			payhere.verify_response(payhere_notification(merchant_id="999999"))

	@pytest.mark.parametrize(
		"missing",
		["merchant_id", "order_id", "payhere_amount", "payhere_currency", "status_code", "md5sig"],
	)
	def test_rejects_missing_fields(self, payhere_settings, payhere_notification, missing):
		payload = payhere_notification()
		payload[missing] = ""

		with pytest.raises(frappe.ValidationError, match="Missing required PayHere"):
			payhere.verify_response(payload)

	def test_missing_field_message_names_them(self, payhere_settings):
		with pytest.raises(frappe.ValidationError, match="merchant_id, order_id"):
			payhere.verify_response(frappe._dict({}))

	def test_lowercase_md5sig_accepted(self, payhere_settings, payhere_notification):
		payload = payhere_notification()
		payload["md5sig"] = payload["md5sig"].lower()

		assert payhere.verify_response(payload)["status"] == "Paid"

	def test_amount_is_reported_for_the_caller_to_check(self, payhere_settings, payhere_notification):
		result = payhere.verify_response(payhere_notification(amount="2500.00", currency="USD"))

		assert result["amount"] == "2500.00"
		assert result["currency"] == "USD"
