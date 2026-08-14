import base64

import pytest

import frappe
from sl_payment_gateways.gateways import webxpay

from .conftest import WEBXPAY_SECRET

GOOD_RESPONSE = "SO-0001|WX-REF-99|2026-08-14 10:00:00|00|Approved|VISA"


def decrypt_payment_field(payment_b64, rsa_key):
	"""Undo what build_checkout() produced, using the private half of the
	keypair - i.e. do what WebXPay's own server does."""
	k = (rsa_key.n.bit_length() + 7) // 8
	c = int.from_bytes(base64.b64decode(payment_b64), "big")
	em = pow(c, rsa_key.d, rsa_key.n).to_bytes(k, "big")

	assert em[0:2] == b"\x00\x02", "not PKCS#1 v1.5 block type 2"
	sep = em.index(b"\x00", 2)
	assert sep - 2 >= 8, "padding shorter than PKCS#1 minimum"
	assert b"\x00" not in em[2:sep], "padding contains a zero byte"

	return em[sep + 1 :].decode("utf-8")


class TestBuildCheckout:
	def test_payment_field_decrypts_to_order_and_amount(self, webxpay_settings, rsa_key):
		result = webxpay.build_checkout("SO-0001", "1500.00", "LKR", {})

		assert decrypt_payment_field(result["fields"]["payment"], rsa_key) == "SO-0001|1500.00"

	def test_amount_is_normalised_before_signing(self, webxpay_settings, rsa_key):
		result = webxpay.build_checkout("SO-0001", 1500.5, "LKR", {})

		assert decrypt_payment_field(result["fields"]["payment"], rsa_key) == "SO-0001|1500.50"

	def test_padding_is_random_per_call(self, webxpay_settings):
		a = webxpay.build_checkout("SO-0001", "1500.00", "LKR", {})
		b = webxpay.build_checkout("SO-0001", "1500.00", "LKR", {})

		assert a["fields"]["payment"] != b["fields"]["payment"]

	def test_order_id_cannot_smuggle_an_amount(self, webxpay_settings):
		# The attack this guards: "SO-0001|0.01" would make the gateway
		# read the amount field as 0.01 and ignore the real one.
		with pytest.raises(frappe.ValidationError):
			webxpay.build_checkout("SO-0001|0.01", "1500.00", "LKR", {})

	def test_sandbox_and_live_urls(self, webxpay_settings):
		assert "stagingxpay.info" in webxpay.build_checkout("SO-1", "1.00", "LKR", {})["checkout_url"]

		webxpay_settings["use_sandbox"] = 0
		assert "webxpay.com" in webxpay.build_checkout("SO-1", "1.00", "LKR", {})["checkout_url"]

	def test_customer_fields_are_cleaned_and_truncated(self, webxpay_settings):
		fields = webxpay.build_checkout(
			"SO-1",
			"1.00",
			"LKR",
			{"first_name": "R" * 60, "last_name": None, "email": "a@b.lk\r\nX: y", "organization": "UCSC"},
		)["fields"]

		assert fields["first_name"] == "R" * 30
		assert fields["last_name"] == "-"
		assert "\r" not in fields["email"] and "\n" not in fields["email"]
		assert fields["address_line_one"] == "UCSC"

	def test_secret_key_is_included_for_the_gateway_form(self, webxpay_settings):
		# WebXPay's integration genuinely requires this in the POST body.
		# Guarded by create_payment() not being a public endpoint.
		assert webxpay.build_checkout("SO-1", "1.00", "LKR", {})["fields"]["secret_key"] == WEBXPAY_SECRET

	def test_unconfigured_settings_throw(self, webxpay_settings):
		webxpay_settings["public_key"] = None
		with pytest.raises(frappe.ValidationError):
			webxpay.build_checkout("SO-1", "1.00", "LKR", {})

	def test_missing_doctype_throws_clearly(self):
		with pytest.raises(frappe.ValidationError, match="does not exist"):
			webxpay.build_checkout("SO-1", "1.00", "LKR", {})

	# A plaintext too big for the key must fail loudly, not truncate.
	# Unreachable through build_checkout() now that order_id and amount are
	# length-bounded (100 + 1 + 12 bytes fits any key of 1024 bits or more),
	# so the guard is exercised directly.
	def test_oversized_plaintext_rejected(self, rsa_key):
		k = (rsa_key.n.bit_length() + 7) // 8

		with pytest.raises(frappe.ValidationError, match="too long"):
			webxpay._rsa_encrypt_pkcs1_type2(b"x" * (k - 10), rsa_key.publickey())

	def test_largest_permitted_order_fits_a_2048_bit_key(self, webxpay_settings, rsa_key):
		# The boundary the length limits are chosen against.
		order_id = "S" * 100
		result = webxpay.build_checkout(order_id, "100000000.00", "LKR", {})

		assert decrypt_payment_field(result["fields"]["payment"], rsa_key) == "%s|100000000.00" % order_id


class TestVerifyResponse:
	def test_accepts_a_genuine_signed_response(self, webxpay_settings, sign_webxpay):
		result = webxpay.verify_response(frappe._dict(sign_webxpay(GOOD_RESPONSE)))

		assert result["order_id"] == "SO-0001"
		assert result["status"] == "Paid"
		assert result["raw"]["payment_gateway_used"] == "VISA"

	@pytest.mark.parametrize("code", ["0", "00"])
	def test_success_status_codes(self, webxpay_settings, sign_webxpay, code):
		payload = "SO-0001|REF|2026-08-14|%s|Approved|VISA" % code
		assert webxpay.verify_response(frappe._dict(sign_webxpay(payload)))["status"] == "Paid"

	@pytest.mark.parametrize("code", ["1", "-1", "99", "000", ""])
	def test_other_status_codes_are_failures(self, webxpay_settings, sign_webxpay, code):
		payload = "SO-0001|REF|2026-08-14|%s|Declined|VISA" % code
		assert webxpay.verify_response(frappe._dict(sign_webxpay(payload)))["status"] == "Failed"

	def test_rejects_payment_tampered_after_signing(self, webxpay_settings, sign_webxpay):
		# The core attack: keep the real signature, swap the payment blob
		# for one saying the order succeeded.
		signed = sign_webxpay(GOOD_RESPONSE)
		signed["payment"] = base64.b64encode(
			b"SO-9999|REF|2026-08-14 10:00:00|00|Approved|VISA"
		).decode("ascii")

		with pytest.raises(frappe.ValidationError, match="signature does not match"):
			webxpay.verify_response(frappe._dict(signed))

	def test_rejects_signature_from_another_payload(self, webxpay_settings, sign_webxpay):
		signed = sign_webxpay(GOOD_RESPONSE, signed_plaintext="SO-0002|REF|2026-08-14|00|x|VISA")

		with pytest.raises(frappe.ValidationError, match="signature does not match"):
			webxpay.verify_response(frappe._dict(signed))

	def test_rejects_signature_from_a_different_key(self, webxpay_settings, sign_webxpay):
		from Crypto.PublicKey import RSA

		signed = sign_webxpay(GOOD_RESPONSE)
		webxpay_settings["public_key"] = RSA.generate(2048).publickey().export_key().decode()

		with pytest.raises(frappe.ValidationError):
			webxpay.verify_response(frappe._dict(signed))

	def test_rejects_unsigned_payload(self, webxpay_settings):
		# Plain "here is my payment blob, trust me".
		payload = {
			"payment": base64.b64encode(GOOD_RESPONSE.encode()).decode(),
			"signature": base64.b64encode(b"\x00" * 256).decode(),
		}

		with pytest.raises(frappe.ValidationError):
			webxpay.verify_response(frappe._dict(payload))

	def test_rejects_short_padding_signature(self, webxpay_settings, rsa_key):
		# PKCS#1 v1.5 mandates >= 8 padding bytes; a sender choosing fewer
		# is not following the scheme and must not be accepted.
		message = GOOD_RESPONSE.encode()
		k = (rsa_key.n.bit_length() + 7) // 8
		em = b"\x00\x01" + b"\xff" * 3 + b"\x00" + b"\x00" * (k - len(message) - 6) + message
		sig = pow(int.from_bytes(em, "big"), rsa_key.d, rsa_key.n).to_bytes(k, "big")

		payload = {
			"payment": base64.b64encode(GOOD_RESPONSE.encode()).decode(),
			"signature": base64.b64encode(sig).decode(),
		}

		with pytest.raises(frappe.ValidationError, match="padding"):
			webxpay.verify_response(frappe._dict(payload))

	@pytest.mark.parametrize(
		"payload",
		[
			{},
			{"payment": "aGk="},
			{"signature": "aGk="},
			{"payment": "", "signature": ""},
		],
	)
	def test_rejects_missing_fields(self, webxpay_settings, payload):
		with pytest.raises(frappe.ValidationError, match="Missing payment or signature"):
			webxpay.verify_response(frappe._dict(payload))

	def test_rejects_malformed_base64(self, webxpay_settings):
		with pytest.raises(frappe.ValidationError, match="base64"):
			webxpay.verify_response(frappe._dict({"payment": "!!!", "signature": "!!!"}))

	def test_rejects_wrong_field_count(self, webxpay_settings, sign_webxpay):
		# Correctly signed, but not the shape we parse - must be an error,
		# not a silent "Failed" with half the fields missing.
		signed = sign_webxpay("SO-0001|REF")

		with pytest.raises(frappe.ValidationError, match="Unexpected WebXPay response format"):
			webxpay.verify_response(frappe._dict(signed))

	def test_reports_no_amount_to_check(self, webxpay_settings, sign_webxpay):
		result = webxpay.verify_response(frappe._dict(sign_webxpay(GOOD_RESPONSE)))

		assert result["amount"] is None
		assert result["currency"] is None

	def test_verification_does_not_read_the_secret_key(self, webxpay_settings, sign_webxpay):
		# verify_response is guest-reachable; it has no business touching
		# the credential store.
		def explode(*args, **kwargs):
			raise AssertionError("verify_response read the merchant secret")

		object.__setattr__(webxpay_settings, "get_password", explode)

		assert webxpay.verify_response(frappe._dict(sign_webxpay(GOOD_RESPONSE)))["status"] == "Paid"
