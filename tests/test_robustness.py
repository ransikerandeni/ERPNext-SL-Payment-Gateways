"""Adversarial input tests.

Two properties matter for anything a gateway (or anyone posing as one)
can reach without a session:

  1. Junk in must never come out as "Paid".
  2. Junk in must raise a *clean* frappe.throw, not an unhandled
     TypeError/AttributeError. An unexpected exception type is how a
     stack trace ends up in an HTTP response, and how a caller's
     `except frappe.ValidationError` quietly stops catching things.
"""

import base64
import os
import random
import string

import pytest

import frappe
from sl_payment_gateways.gateways import payhere, webxpay

# Exceptions a caller is entitled to see. Anything else is a bug.
CLEAN = (frappe.ValidationError, frappe.DoesNotExistError)

HOSTILE_STRINGS = [
	"",
	" ",
	"0",
	"-1",
	"null",
	"None",
	"undefined",
	"NaN",
	"Infinity",
	"1e999",
	"a" * 10000,
	"../../etc/passwd",
	"<script>alert(1)</script>",
	"' OR '1'='1",
	"{{7*7}}",
	"%s%d%n",
	"\x00\x01\x02",
	"\r\nX-Injected: yes",
	"SO-1|999999.00",
	"🙂",
	"‮evil",
]

HOSTILE_VALUES = HOSTILE_STRINGS + [None, 0, -1, 1.5, [], {}, ["a", "b"], True]


def random_form_dict(rng, keys):
	return frappe._dict({key: rng.choice(HOSTILE_VALUES) for key in keys if rng.random() > 0.2})


class TestWebXPayHostileInput:
	@pytest.mark.parametrize("payment", HOSTILE_STRINGS)
	@pytest.mark.parametrize("signature", ["", "!!!", "aGk=", "AAAA"])
	def test_never_verifies(self, webxpay_settings, payment, signature):
		with pytest.raises(CLEAN):
			webxpay.verify_response(frappe._dict({"payment": payment, "signature": signature}))

	def test_random_signatures_never_verify(self, webxpay_settings, rsa_key):
		k = (rsa_key.n.bit_length() + 7) // 8
		rng = random.Random(20260814)

		for _ in range(200):
			payload = {
				"payment": base64.b64encode(b"SO-0001|REF|D|00|ok|VISA").decode(),
				"signature": base64.b64encode(bytes(rng.randrange(256) for _ in range(k))).decode(),
			}
			with pytest.raises(CLEAN):
				webxpay.verify_response(frappe._dict(payload))

	def test_random_form_dicts_never_verify(self, webxpay_settings):
		rng = random.Random(1)

		for _ in range(300):
			with pytest.raises(CLEAN):
				webxpay.verify_response(random_form_dict(rng, ["payment", "signature", "order_id", "status"]))

	def test_bit_flips_in_a_real_signature_never_verify(self, webxpay_settings, sign_webxpay):
		signed = sign_webxpay("SO-0001|REF|2026-08-14|00|Approved|VISA")
		raw = bytearray(base64.b64decode(signed["signature"]))
		rng = random.Random(7)

		for _ in range(100):
			flipped = bytearray(raw)
			flipped[rng.randrange(len(flipped))] ^= 1 << rng.randrange(8)
			payload = dict(signed, signature=base64.b64encode(bytes(flipped)).decode())

			with pytest.raises(CLEAN):
				webxpay.verify_response(frappe._dict(payload))

	def test_non_utf8_payment_is_a_clean_error(self, webxpay_settings):
		payload = {
			"payment": base64.b64encode(b"\xff\xfe\xfd").decode(),
			"signature": base64.b64encode(os.urandom(256)).decode(),
		}

		with pytest.raises(CLEAN):
			webxpay.verify_response(frappe._dict(payload))


class TestPayHereHostileInput:
	def test_random_form_dicts_never_verify(self, payhere_settings):
		rng = random.Random(2)
		keys = ["merchant_id", "order_id", "payhere_amount", "payhere_currency", "status_code", "md5sig"]

		for _ in range(500):
			with pytest.raises(CLEAN):
				payhere.verify_response(random_form_dict(rng, keys))

	def test_random_md5sigs_never_verify(self, payhere_settings, payhere_notification):
		rng = random.Random(3)
		alphabet = string.hexdigits.upper()

		for _ in range(300):
			payload = payhere_notification()
			payload["md5sig"] = "".join(rng.choice(alphabet) for _ in range(32))

			with pytest.raises(CLEAN):
				payhere.verify_response(payload)

	def test_single_character_changes_never_verify(self, payhere_settings, payhere_notification):
		genuine = payhere_notification()

		for field in ("merchant_id", "order_id", "payhere_amount", "payhere_currency", "status_code"):
			payload = payhere_notification()
			payload[field] = str(genuine[field]) + "0"

			with pytest.raises(CLEAN):
				payhere.verify_response(payload)

	@pytest.mark.parametrize("field", ["merchant_id", "md5sig"])
	def test_non_ascii_values_are_rejected_not_crashed(self, payhere_settings, payhere_notification, field):
		"""Regression: hmac.compare_digest raises TypeError on a non-ASCII
		str, so this used to be a 500 with a traceback rather than a
		rejection - on an endpoint any guest can reach."""
		payload = payhere_notification()
		payload[field] = "🙂"

		with pytest.raises(CLEAN):
			payhere.verify_response(payload)

	def test_signature_bound_to_every_field(self, payhere_settings, payhere_notification):
		"""A genuine notification for one order must not verify as another.

		Guards the classic mistake of hashing only a subset of the fields.
		"""
		a = payhere_notification(order_id="SO-0001", amount="1500.00")
		b = payhere_notification(order_id="SO-0002", amount="1.00")

		swapped = dict(a)
		swapped["md5sig"] = b["md5sig"]

		with pytest.raises(CLEAN):
			payhere.verify_response(frappe._dict(swapped))


class TestBuildCheckoutHostileInput:
	@pytest.mark.parametrize("order_id", HOSTILE_VALUES)
	def test_webxpay_order_id(self, webxpay_settings, order_id):
		try:
			result = webxpay.build_checkout(order_id, "1500.00", "LKR", {})
		except CLEAN:
			return
		# If it was accepted it must be a plain, delimiter-free id.
		assert "|" not in result["fields"]["payment"]

	@pytest.mark.parametrize("amount", HOSTILE_VALUES)
	def test_amounts(self, webxpay_settings, amount):
		try:
			webxpay.build_checkout("SO-1", amount, "LKR", {})
		except CLEAN:
			return
		# Only genuinely numeric values may get through.
		assert float(str(amount)) > 0

	@pytest.mark.parametrize("currency", HOSTILE_VALUES)
	def test_currencies(self, webxpay_settings, currency):
		try:
			result = webxpay.build_checkout("SO-1", "1.00", currency, {})
		except CLEAN:
			return
		assert result["fields"]["process_currency"].isalpha()

	@pytest.mark.parametrize("value", HOSTILE_VALUES)
	def test_payhere_urls(self, payhere_settings, value):
		try:
			result = payhere.build_checkout("SO-1", "1.00", "LKR", {"notify_url": value})
		except CLEAN:
			return
		assert result["fields"]["notify_url"].startswith("https://erp.example.com")

	@pytest.mark.parametrize("value", HOSTILE_VALUES)
	def test_customer_fields_never_break_the_form(self, payhere_settings, value):
		fields = payhere.build_checkout(
			"SO-1",
			"1.00",
			"LKR",
			{
				"notify_url": "/x",
				"first_name": value,
				"last_name": value,
				"email": value,
				"contact_number": value,
				"address": value,
				"city": value,
				"country": value,
				"items": value,
			},
		)["fields"]

		for key in ("first_name", "last_name", "email", "phone", "address", "city", "country", "items"):
			assert "\r" not in fields[key] and "\n" not in fields[key], key
			assert "\x00" not in fields[key], key
			assert len(fields[key]) <= 100, key
