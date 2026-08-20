import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import frappe_stub  # noqa: E402

# Install the stub before any `import frappe` inside the app runs.
frappe_stub.build_module()

import frappe  # noqa: E402

from sl_payment_gateways.gateways import payhere, webxpay  # noqa: E402

# Sandbox and live are different accounts at both gateways, so the
# fixtures use different values for each - a test that passes with the
# same secret in both slots would prove nothing about mode selection.
WEBXPAY_SANDBOX_SECRET = "webxpay-staging-secret-key"
WEBXPAY_LIVE_SECRET = "webxpay-production-secret-key"

PAYHERE_SANDBOX_MERCHANT_ID = "1211149"
PAYHERE_SANDBOX_SECRET = "MzI0MTU5NDk4NzE5ODc2NTQzMjE"
PAYHERE_LIVE_MERCHANT_ID = "4001337"
PAYHERE_LIVE_SECRET = "OTg3NjU0MzIxMDEyMzQ1Njc4OQ"


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


__all__ = ["webxpay", "payhere"]
