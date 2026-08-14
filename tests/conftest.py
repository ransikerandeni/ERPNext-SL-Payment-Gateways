import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import frappe_stub  # noqa: E402

# Install the stub before any `import frappe` inside the app runs.
frappe_stub.build_module()

import frappe  # noqa: E402

from sl_payment_gateways.gateways import payhere, webxpay  # noqa: E402

WEBXPAY_SECRET = "webxpay-secret-key-value"
PAYHERE_MERCHANT_ID = "1211149"
PAYHERE_SECRET = "MzI0MTU5NDk4NzE5ODc2NTQzMjE"


@pytest.fixture(autouse=True)
def clean_frappe():
	"""Every test starts with an empty form_dict and no configured doctypes."""
	frappe.local.form_dict = frappe._dict()
	frappe.test_docs.clear()
	yield
	frappe.local.form_dict = frappe._dict()
	frappe.test_docs.clear()


@pytest.fixture(scope="session")
def rsa_key():
	"""A real RSA keypair, generated once - stands in for WebXPay's."""
	from Crypto.PublicKey import RSA

	return RSA.generate(2048)


@pytest.fixture
def webxpay_settings(rsa_key):
	frappe.test_docs["WebXPay Settings"] = frappe_stub.FakeSettingsDoc(
		{"public_key": rsa_key.publickey().export_key().decode(), "use_sandbox": 1},
		passwords={"secret_key": WEBXPAY_SECRET},
	)
	return frappe.test_docs["WebXPay Settings"]


@pytest.fixture
def payhere_settings():
	frappe.test_docs["PayHere Settings"] = frappe_stub.FakeSettingsDoc(
		{"merchant_id": PAYHERE_MERCHANT_ID, "use_sandbox": 1},
		passwords={"merchant_secret": PAYHERE_SECRET},
	)
	return frappe.test_docs["PayHere Settings"]


@pytest.fixture
def sign_webxpay(rsa_key):
	"""Produce a genuine WebXPay-style response: base64 plaintext plus a
	signature made by RSA private-encrypting (PKCS#1 v1.5 type 1) the same
	string - i.e. what PHP's openssl_private_encrypt() emits."""
	import base64

	def _sign(plaintext, signed_plaintext=None):
		signed = plaintext if signed_plaintext is None else signed_plaintext
		message = signed.encode("utf-8")

		k = (rsa_key.n.bit_length() + 7) // 8
		padding_len = k - len(message) - 3
		em = b"\x00\x01" + b"\xff" * padding_len + b"\x00" + message
		signature = pow(int.from_bytes(em, "big"), rsa_key.d, rsa_key.n).to_bytes(k, "big")

		return {
			"payment": base64.b64encode(plaintext.encode("utf-8")).decode("ascii"),
			"signature": base64.b64encode(signature).decode("ascii"),
		}

	return _sign


@pytest.fixture
def payhere_notification():
	"""Build a correctly signed PayHere notify_url payload."""

	def _build(order_id="SO-0001", amount="1500.00", currency="LKR", status_code="2", **overrides):
		merchant_id = overrides.pop("merchant_id", PAYHERE_MERCHANT_ID)
		secret_hash = payhere._secret_hash(PAYHERE_SECRET)
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
