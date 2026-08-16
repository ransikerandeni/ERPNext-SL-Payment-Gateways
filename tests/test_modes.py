"""Sandbox vs live credential selection.

Both gateways treat sandbox and live as entirely separate merchant
accounts - PayHere's sandbox is a separate deployment that cannot be
converted to a live one, and WebXPay's staging portal issues its own key
pair. So the Settings doctypes hold both sets and `use_sandbox` picks
one. The failure these tests exist to prevent is the quiet one: going
live while still signing with test credentials, or the reverse.
"""

import pytest

import frappe
from sl_payment_gateways.gateways import payhere, webxpay

from .conftest import (
	PAYHERE_LIVE_MERCHANT_ID,
	PAYHERE_LIVE_SECRET,
	PAYHERE_SANDBOX_MERCHANT_ID,
	PAYHERE_SANDBOX_SECRET,
	WEBXPAY_LIVE_SECRET,
	WEBXPAY_SANDBOX_SECRET,
)
from .test_payhere import NOTIFY, reference_checkout_hash
from .test_webxpay import GOOD_RESPONSE, decrypt_payment_field


class TestWebXPayModes:
	def test_sandbox_uses_the_staging_portal_and_its_credentials(self, webxpay_settings, rsa_key):
		result = webxpay.build_checkout("SO-1", "1500.00", "LKR", {})

		assert result["checkout_url"] == webxpay.SANDBOX_CHECKOUT_URL
		assert result["fields"]["secret_key"] == WEBXPAY_SANDBOX_SECRET
		# Encrypted to the staging key, so only the staging private key opens it.
		assert decrypt_payment_field(result["fields"]["payment"], rsa_key) == "SO-1|1500.00"

	def test_live_uses_the_production_portal_and_its_credentials(self, webxpay_settings, rsa_key_live):
		webxpay_settings["use_sandbox"] = 0

		result = webxpay.build_checkout("SO-1", "1500.00", "LKR", {})

		assert result["checkout_url"] == webxpay.LIVE_CHECKOUT_URL
		assert result["fields"]["secret_key"] == WEBXPAY_LIVE_SECRET
		assert decrypt_payment_field(result["fields"]["payment"], rsa_key_live) == "SO-1|1500.00"

	def test_live_checkout_is_not_encrypted_to_the_sandbox_key(self, webxpay_settings, rsa_key):
		webxpay_settings["use_sandbox"] = 0

		result = webxpay.build_checkout("SO-1", "1500.00", "LKR", {})

		# The staging private key must not be able to read a live checkout.
		# Decrypting with the wrong key yields bytes that fail the PKCS#1
		# structure assertions (or aren't UTF-8) - either way, not the order.
		with pytest.raises((AssertionError, ValueError, UnicodeDecodeError)):
			decrypt_payment_field(result["fields"]["payment"], rsa_key)

	def test_sandbox_signed_response_is_rejected_in_live_mode(self, webxpay_settings, sign_webxpay):
		"""The one that matters: a staging response replayed at a live site."""
		signed = sign_webxpay(GOOD_RESPONSE)

		assert webxpay.verify_response(frappe._dict(signed))["status"] == "Paid"

		webxpay_settings["use_sandbox"] = 0

		with pytest.raises(frappe.ValidationError):
			webxpay.verify_response(frappe._dict(signed))

	def test_live_signed_response_verifies_only_in_live_mode(
		self, webxpay_settings, sign_webxpay, rsa_key_live
	):
		signed = sign_webxpay(GOOD_RESPONSE, key=rsa_key_live)

		with pytest.raises(frappe.ValidationError):
			webxpay.verify_response(frappe._dict(signed))

		webxpay_settings["use_sandbox"] = 0

		assert webxpay.verify_response(frappe._dict(signed))["status"] == "Paid"

	def test_missing_live_key_fails_rather_than_using_the_sandbox_one(self, webxpay_settings):
		webxpay_settings["use_sandbox"] = 0
		webxpay_settings["live_public_key"] = None

		with pytest.raises(frappe.ValidationError, match="live_public_key"):
			webxpay.build_checkout("SO-1", "1.00", "LKR", {})

	def test_missing_live_secret_fails_rather_than_using_the_sandbox_one(self, webxpay_settings):
		webxpay_settings["use_sandbox"] = 0
		object.__setattr__(webxpay_settings, "_passwords", {"sandbox_secret_key": WEBXPAY_SANDBOX_SECRET})

		with pytest.raises(frappe.ValidationError, match="live_secret_key"):
			webxpay.build_checkout("SO-1", "1.00", "LKR", {})

	def test_error_names_the_mode_and_the_field_to_fill(self, webxpay_settings):
		webxpay_settings["sandbox_public_key"] = None

		with pytest.raises(frappe.ValidationError) as excinfo:
			webxpay.build_checkout("SO-1", "1.00", "LKR", {})

		message = str(excinfo.value)
		assert "WebXPay Settings" in message
		assert "Sandbox" in message
		assert "sandbox_public_key" in message


class TestPayHereModes:
	def test_sandbox_uses_the_sandbox_account(self, payhere_settings):
		result = payhere.build_checkout("SO-1", "1500.00", "LKR", {"notify_url": NOTIFY})

		assert result["checkout_url"] == payhere.SANDBOX_CHECKOUT_URL
		assert result["fields"]["merchant_id"] == PAYHERE_SANDBOX_MERCHANT_ID
		assert result["fields"]["hash"] == reference_checkout_hash(
			PAYHERE_SANDBOX_MERCHANT_ID, "SO-1", "1500.00", "LKR", PAYHERE_SANDBOX_SECRET
		)

	def test_live_uses_the_live_account(self, payhere_settings):
		payhere_settings["use_sandbox"] = 0

		result = payhere.build_checkout("SO-1", "1500.00", "LKR", {"notify_url": NOTIFY})

		assert result["checkout_url"] == payhere.LIVE_CHECKOUT_URL
		assert result["fields"]["merchant_id"] == PAYHERE_LIVE_MERCHANT_ID
		assert result["fields"]["hash"] == reference_checkout_hash(
			PAYHERE_LIVE_MERCHANT_ID, "SO-1", "1500.00", "LKR", PAYHERE_LIVE_SECRET
		)

	def test_live_hash_is_not_the_sandbox_hash(self, payhere_settings):
		sandbox = payhere.build_checkout("SO-1", "1500.00", "LKR", {"notify_url": NOTIFY})["fields"]["hash"]

		payhere_settings["use_sandbox"] = 0
		live = payhere.build_checkout("SO-1", "1500.00", "LKR", {"notify_url": NOTIFY})["fields"]["hash"]

		assert sandbox != live

	def test_sandbox_notification_is_rejected_in_live_mode(self, payhere_settings, payhere_notification):
		"""A sandbox webhook replayed at a live site: caught on merchant_id."""
		payload = payhere_notification()

		assert payhere.verify_response(payload)["status"] == "Paid"

		payhere_settings["use_sandbox"] = 0

		with pytest.raises(frappe.ValidationError, match="merchant_id mismatch"):
			payhere.verify_response(payload)

	def test_live_notification_is_rejected_in_sandbox_mode(self, payhere_settings, payhere_notification):
		payload = payhere_notification(
			merchant_id=PAYHERE_LIVE_MERCHANT_ID, secret=PAYHERE_LIVE_SECRET
		)

		with pytest.raises(frappe.ValidationError, match="merchant_id mismatch"):
			payhere.verify_response(payload)

		payhere_settings["use_sandbox"] = 0

		assert payhere.verify_response(payload)["status"] == "Paid"

	def test_right_merchant_id_but_sandbox_secret_still_fails_in_live_mode(
		self, payhere_settings, payhere_notification
	):
		# Correct live merchant id, signed with the sandbox secret.
		payload = payhere_notification(
			merchant_id=PAYHERE_LIVE_MERCHANT_ID, secret=PAYHERE_SANDBOX_SECRET
		)
		payhere_settings["use_sandbox"] = 0

		with pytest.raises(frappe.ValidationError, match="md5sig verification failed"):
			payhere.verify_response(payload)

	def test_missing_live_credentials_fail_rather_than_falling_back(self, payhere_settings):
		payhere_settings["use_sandbox"] = 0
		payhere_settings["live_merchant_id"] = None

		with pytest.raises(frappe.ValidationError, match="live_merchant_id"):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})

	def test_missing_live_secret_fails_rather_than_falling_back(self, payhere_settings):
		payhere_settings["use_sandbox"] = 0
		object.__setattr__(
			payhere_settings, "_passwords", {"sandbox_merchant_secret": PAYHERE_SANDBOX_SECRET}
		)

		with pytest.raises(frappe.ValidationError, match="live_merchant_secret"):
			payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})


class TestLegacySingleCredentialSet:
	"""Installs configured before the split keep working: the unprefixed
	field is used for whichever mode is active."""

	def test_webxpay_legacy_fields_still_work(self, webxpay_legacy_settings, rsa_key):
		result = webxpay.build_checkout("SO-1", "1500.00", "LKR", {})

		assert result["fields"]["secret_key"] == WEBXPAY_SANDBOX_SECRET
		assert decrypt_payment_field(result["fields"]["payment"], rsa_key) == "SO-1|1500.00"

	def test_webxpay_legacy_fields_are_used_in_live_mode_too(self, webxpay_legacy_settings, rsa_key):
		webxpay_legacy_settings["use_sandbox"] = 0

		result = webxpay.build_checkout("SO-1", "1500.00", "LKR", {})

		assert result["checkout_url"] == webxpay.LIVE_CHECKOUT_URL
		assert result["fields"]["secret_key"] == WEBXPAY_SANDBOX_SECRET

	def test_payhere_legacy_fields_still_work(self, payhere_legacy_settings):
		result = payhere.build_checkout("SO-1", "1500.00", "LKR", {"notify_url": NOTIFY})

		assert result["fields"]["merchant_id"] == PAYHERE_SANDBOX_MERCHANT_ID
		assert result["fields"]["hash"] == reference_checkout_hash(
			PAYHERE_SANDBOX_MERCHANT_ID, "SO-1", "1500.00", "LKR", PAYHERE_SANDBOX_SECRET
		)

	def test_prefixed_field_wins_over_legacy(self, payhere_legacy_settings):
		payhere_legacy_settings["sandbox_merchant_id"] = "9999999"

		result = payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": NOTIFY})

		assert result["fields"]["merchant_id"] == "9999999"

	def test_legacy_notification_still_verifies(self, payhere_legacy_settings, payhere_notification):
		assert payhere.verify_response(payhere_notification())["status"] == "Paid"
