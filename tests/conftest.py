import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import frappe_stub  # noqa: E402

# Install the stub before any `import frappe` inside the app runs.
frappe_stub.build_module()

import frappe  # noqa: E402

from sl_payment_gateways.gateways import payhere, peoples_bank, webxpay  # noqa: E402

# Sandbox and live are different accounts at both gateways, so the
# fixtures use different values for each - a test that passes with the
# same secret in both slots would prove nothing about mode selection.
WEBXPAY_SANDBOX_SECRET = "webxpay-staging-secret-key"
WEBXPAY_LIVE_SECRET = "webxpay-production-secret-key"

PAYHERE_SANDBOX_MERCHANT_ID = "1211149"
PAYHERE_SANDBOX_SECRET = "MzI0MTU5NDk4NzE5ODc2NTQzMjE"
PAYHERE_LIVE_MERCHANT_ID = "4001337"
PAYHERE_LIVE_SECRET = "OTg3NjU0MzIxMDEyMzQ1Njc4OQ"

# People's Bank issues a CyberSource test profile and a separate live one.
# Invented values, shaped like the real thing (profile id a UUID, access
# key 32 hex, secret key a long hex string) so the tests exercise
# realistic lengths and character sets without carrying any part of a
# credential from the bank's integration pack into the repository.
PEOPLES_SANDBOX_PROFILE_ID = "1A2B3C4D-0000-4AAA-8BBB-0123456789AB"
PEOPLES_SANDBOX_ACCESS_KEY = "aaaaaaaa1111bbbbbbbb2222cccccccc"
PEOPLES_SANDBOX_SECRET = "0" * 40 + "sandbox" + "f" * 17

PEOPLES_LIVE_PROFILE_ID = "9F8E7D6C-0000-4CCC-8DDD-BA9876543210"
PEOPLES_LIVE_ACCESS_KEY = "dddddddd3333eeeeeeee4444ffffffff"
PEOPLES_LIVE_SECRET = "1" * 40 + "live" + "e" * 20


@pytest.fixture(autouse=True)
def clean_frappe():
	"""Every test starts with an empty form_dict and no configured doctypes."""
	frappe.local.form_dict = frappe._dict()
	frappe.local.request = None
	frappe.local.cookie_manager = None
	frappe.test_docs.clear()
	yield
	frappe.local.form_dict = frappe._dict()
	frappe.local.request = None
	frappe.local.cookie_manager = None
	frappe.test_docs.clear()


@pytest.fixture(scope="session")
def rsa_key():
	"""The sandbox keypair - stands in for WebXPay staging's."""
	from Crypto.PublicKey import RSA

	return RSA.generate(2048)


@pytest.fixture(scope="session")
def rsa_key_live():
	"""A different keypair, for the live portal."""
	from Crypto.PublicKey import RSA

	return RSA.generate(2048)


@pytest.fixture
def webxpay_settings(rsa_key, rsa_key_live):
	"""Both credential sets present, sandbox active."""
	frappe.test_docs["WebXPay Settings"] = frappe_stub.FakeSettingsDoc(
		{
			"use_sandbox": 1,
			"sandbox_public_key": rsa_key.publickey().export_key().decode(),
			"live_public_key": rsa_key_live.publickey().export_key().decode(),
		},
		passwords={
			"sandbox_secret_key": WEBXPAY_SANDBOX_SECRET,
			"live_secret_key": WEBXPAY_LIVE_SECRET,
		},
	)
	return frappe.test_docs["WebXPay Settings"]


@pytest.fixture
def webxpay_legacy_settings(rsa_key):
	"""The pre-split shape: one unprefixed credential set. Kept so the
	backward-compatibility fallback stays covered."""
	frappe.test_docs["WebXPay Settings"] = frappe_stub.FakeSettingsDoc(
		{"use_sandbox": 1, "public_key": rsa_key.publickey().export_key().decode()},
		passwords={"secret_key": WEBXPAY_SANDBOX_SECRET},
	)
	return frappe.test_docs["WebXPay Settings"]


@pytest.fixture
def payhere_settings():
	"""Both credential sets present, sandbox active."""
	frappe.test_docs["PayHere Settings"] = frappe_stub.FakeSettingsDoc(
		{
			"use_sandbox": 1,
			"sandbox_merchant_id": PAYHERE_SANDBOX_MERCHANT_ID,
			"live_merchant_id": PAYHERE_LIVE_MERCHANT_ID,
		},
		passwords={
			"sandbox_merchant_secret": PAYHERE_SANDBOX_SECRET,
			"live_merchant_secret": PAYHERE_LIVE_SECRET,
		},
	)
	return frappe.test_docs["PayHere Settings"]


@pytest.fixture
def payhere_legacy_settings():
	frappe.test_docs["PayHere Settings"] = frappe_stub.FakeSettingsDoc(
		{"use_sandbox": 1, "merchant_id": PAYHERE_SANDBOX_MERCHANT_ID},
		passwords={"merchant_secret": PAYHERE_SANDBOX_SECRET},
	)
	return frappe.test_docs["PayHere Settings"]


@pytest.fixture
def sign_webxpay(rsa_key):
	"""Produce a genuine WebXPay-style response: base64 plaintext plus a
	signature made by RSA private-encrypting (PKCS#1 v1.5 type 1) the same
	string - i.e. what PHP's openssl_private_encrypt() emits."""
	import base64

	def _sign(plaintext, signed_plaintext=None, key=None):
		key = key or rsa_key
		signed = plaintext if signed_plaintext is None else signed_plaintext
		message = signed.encode("utf-8")

		k = (key.n.bit_length() + 7) // 8
		padding_len = k - len(message) - 3
		em = b"\x00\x01" + b"\xff" * padding_len + b"\x00" + message
		signature = pow(int.from_bytes(em, "big"), key.d, key.n).to_bytes(k, "big")

		return {
			"payment": base64.b64encode(plaintext.encode("utf-8")).decode("ascii"),
			"signature": base64.b64encode(signature).decode("ascii"),
		}

	return _sign


@pytest.fixture
def payhere_notification():
	"""Build a correctly signed PayHere notify_url payload."""

	def _build(
		order_id="SO-0001",
		amount="1500.00",
		currency="LKR",
		status_code="2",
		merchant_id=None,
		secret=None,
		**overrides,
	):
		merchant_id = merchant_id or PAYHERE_SANDBOX_MERCHANT_ID
		secret_hash = payhere._secret_hash(secret or PAYHERE_SANDBOX_SECRET)
		md5sig = payhere._md5_upper(
			"%s%s%s%s%s%s" % (merchant_id, order_id, amount, currency, status_code, secret_hash)
		)
		payload = frappe._dict(
			{
				"merchant_id": merchant_id,
				"order_id": order_id,
				"payhere_amount": amount,
				"payhere_currency": currency,
				"status_code": status_code,
				"md5sig": md5sig,
			}
		)
		payload.update(overrides)
		return payload

	return _build


@pytest.fixture
def peoples_bank_settings():
	"""Both credential sets present, sandbox active."""
	frappe.test_docs["Peoples Bank Settings"] = frappe_stub.FakeSettingsDoc(
		{
			"use_sandbox": 1,
			"sandbox_profile_id": PEOPLES_SANDBOX_PROFILE_ID,
			"sandbox_access_key": PEOPLES_SANDBOX_ACCESS_KEY,
			"live_profile_id": PEOPLES_LIVE_PROFILE_ID,
			"live_access_key": PEOPLES_LIVE_ACCESS_KEY,
		},
		passwords={
			"sandbox_secret_key": PEOPLES_SANDBOX_SECRET,
			"live_secret_key": PEOPLES_LIVE_SECRET,
		},
	)
	return frappe.test_docs["Peoples Bank Settings"]


@pytest.fixture
def peoples_bank_legacy_settings():
	"""The pre-split shape: one unprefixed credential set."""
	frappe.test_docs["Peoples Bank Settings"] = frappe_stub.FakeSettingsDoc(
		{
			"use_sandbox": 1,
			"profile_id": PEOPLES_SANDBOX_PROFILE_ID,
			"access_key": PEOPLES_SANDBOX_ACCESS_KEY,
		},
		passwords={"secret_key": PEOPLES_SANDBOX_SECRET},
	)
	return frappe.test_docs["Peoples Bank Settings"]


@pytest.fixture
def peoples_bank_response():
	"""Build a genuinely signed Secure Acceptance response POST.

	Mirrors the shape of the real payloads in People's Bank's integration
	pack (ResponseParams Success.txt / Fail.txt): a signed field list that
	names itself, plus unsigned extras CyberSource also posts.
	"""

	def _build(
		order_id="SO-0001",
		decision="ACCEPT",
		amount="1500.00",
		auth_amount=None,
		currency="LKR",
		profile_id=None,
		secret=None,
		signed_field_names=None,
		extra_signed=None,
		**unsigned
	):
		profile_id = PEOPLES_SANDBOX_PROFILE_ID if profile_id is None else profile_id

		signed = {
			"transaction_id": "6660635047636132804251",
			"decision": decision,
			"req_access_key": PEOPLES_SANDBOX_ACCESS_KEY,
			"req_profile_id": profile_id,
			"req_transaction_uuid": "634e1c29e3e95",
			"req_transaction_type": "sale",
			"req_reference_number": order_id,
			"req_amount": amount,
			"req_currency": currency,
			"req_locale": "en",
			"reason_code": "100",
			"message": "Request was processed successfully.",
			"signed_date_time": "2026-09-07T03:25:06Z",
		}
		if auth_amount is not None:
			signed["auth_amount"] = auth_amount
		signed.update(extra_signed or {})

		names = signed_field_names or ([*signed, "signed_field_names"])

		payload = frappe._dict(signed)
		payload["signed_field_names"] = ",".join(names)
		payload["signature"] = peoples_bank._sign(
			peoples_bank._data_to_sign(payload, names), secret or PEOPLES_SANDBOX_SECRET
		)
		payload.update(unsigned)
		return payload

	return _build


__all__ = ["webxpay", "payhere", "peoples_bank"]
