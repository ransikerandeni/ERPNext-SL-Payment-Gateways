"""WebXPay (webxpay.com) - Redirect Integration.

RSA-encrypts the payment string with WebXPay's public key for checkout,
and verifies an RSA-signed response on the way back. Settings (public
key, secret key, sandbox toggle) live in the "WebXPay Settings" single
doctype.

Padding: WebXPay's own sample PHP code (php-request.txt / php-response.txt
from their developer portal) calls openssl_public_encrypt() and
openssl_public_decrypt() with no padding argument, which defaults to
OPENSSL_PKCS1_PADDING (PKCS#1 v1.5) - block type 0x02 for encryption,
block type 0x01 for the signature-style public-decrypt. That's what's
implemented below.

NOTE: the response field order below is based on WebXPay's worked
example on their Redirect Integration guide page, not a fully confirmed
reading of their sample code - verify against your actual downloaded
php-response.txt before relying on this for real transactions.
"""

import base64
import os

import frappe
from Crypto.PublicKey import RSA


def _settings():
	settings = frappe.get_doc("WebXPay Settings")
	if not settings.public_key or not settings.secret_key:
		frappe.throw("WebXPay Settings is not configured.")
	return settings


def _public_key(settings):
	return RSA.import_key(settings.public_key)


def _i2osp(n, length):
	return n.to_bytes(length, "big")


def _os2ip(b):
	return int.from_bytes(b, "big")


def _rsa_encrypt_pkcs1_type2(plaintext: bytes, key: RSA.RsaKey) -> bytes:
	"""PKCS#1 v1.5 block type 0x02 (encryption) padding - equivalent to
	PHP's openssl_public_encrypt() with default padding."""
	n, e = key.n, key.e
	k = (n.bit_length() + 7) // 8

	if len(plaintext) > k - 11:
		frappe.throw("Plaintext too long for this RSA key size.")

	padding_len = k - len(plaintext) - 3
	ps = bytearray()
	while len(ps) < padding_len:
		b = os.urandom(1)
		if b != b"\x00":
			ps += b

	em = b"\x00\x02" + bytes(ps) + b"\x00" + plaintext
	c = pow(_os2ip(em), e, n)
	return _i2osp(c, k)


def _rsa_public_decrypt_pkcs1_type1(ciphertext: bytes, key: RSA.RsaKey) -> bytes:
	"""PKCS#1 v1.5 block type 0x01 (signature) padding, reversed with the
	public key - equivalent to PHP's openssl_public_decrypt() verifying
	data produced by openssl_private_encrypt()."""
	n, e = key.n, key.e
	k = (n.bit_length() + 7) // 8

	c = _os2ip(ciphertext)
	if c >= n:
		frappe.throw("Ciphertext out of range for this RSA key.")

	em = _i2osp(pow(c, e, n), k)

	if em[0:2] != b"\x00\x01":
		frappe.throw("Invalid PKCS#1 signature padding (block type).")

	idx = 2
	while idx < len(em) and em[idx] == 0xFF:
		idx += 1

	if idx >= len(em) or em[idx] != 0x00:
		frappe.throw("Invalid PKCS#1 signature padding (terminator).")

	return em[idx + 1 :]


def build_checkout(order_id, amount, currency, customer):
	settings = _settings()
	key = _public_key(settings)

	plaintext = ("%s|%s" % (order_id, amount)).encode("utf-8")
	ciphertext = _rsa_encrypt_pkcs1_type2(plaintext, key)
	payment_field = base64.b64encode(ciphertext).decode("ascii")

	checkout_url = (
		"https://stagingxpay.info/index.php?route=checkout/billing"
		if settings.use_sandbox
		else "https://webxpay.com/index.php?route=checkout/billing"
	)

	return {
		"method": "POST",
		"checkout_url": checkout_url,
		"fields": {
			# WebXPay requires an address but we don't collect one from
			# Participant - placeholder until a real field exists.
			"address_line_one": customer.get("organization") or "N/A",
			"first_name": (customer.get("first_name") or "Participant")[:30],
			"last_name": (customer.get("last_name") or "-")[:30],
			"email": customer.get("email"),
			"contact_number": customer.get("contact_number"),
			"secret_key": settings.get_password("secret_key"),
			"payment": payment_field,
			"cms": "ERPNext",
			"process_currency": currency,
		},
	}


def verify_response(form_dict):
	payment = form_dict.get("payment")
	signature = form_dict.get("signature")

	if not payment or not signature:
		frappe.throw("Missing payment or signature.")

	settings = _settings()
	key = _public_key(settings)

	payment_plaintext = base64.b64decode(payment).decode("utf-8")
	signature_plaintext = _rsa_public_decrypt_pkcs1_type1(
		base64.b64decode(signature), key
	).decode("utf-8")

	if signature_plaintext != payment_plaintext:
		frappe.throw("WebXPay response signature does not match payment data.")

	parts = payment_plaintext.split("|")
	fields = [
		"order_id",
		"order_reference_number",
		"date_time_transaction",
		"status_code",
		"comment",
		"payment_gateway_used",
	]
	parsed = dict(zip(fields, parts))

	status = "Paid" if parsed.get("status_code") in ("0", "00") else "Failed"

	return {
		"order_id": parsed.get("order_id"),
		"status": status,
		"raw": parsed,
	}
