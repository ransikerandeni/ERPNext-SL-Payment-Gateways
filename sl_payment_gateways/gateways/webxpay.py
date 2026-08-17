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

Response format: confirmed against WebXPay's Redirect Integration guide
(developers.webxpay.com/Guides/Redirect-Integration), which documents the
decoded payment string as
order_id|order_reference_number|date_time_transaction|status_code|comment|payment_gateway_used
and status codes 0/00 = approved, 15 = declined.

Sandbox and live are separate WebXPay portals (stagingxpay.info and
webxpay.com) with separate merchant accounts and separate key pairs, so
"WebXPay Settings" holds both sets and `use_sandbox` picks one - see
docs/webxpay.md.

RESIDUAL RISKS you must handle in your own return handler
---------------------------------------------------------
1. The response carries no amount. verify_response() therefore proves
   only "WebXPay says order X reached status Y" - never "order X was paid
   the right amount". Re-derive the expected price yourself and treat the
   gateway's own transaction record/dashboard as the source of truth for
   the figure.
2. The response carries no merchant identifier, and WebXPay signs with
   its own key, not a per-merchant one. A validly signed response for a
   given order_id is therefore not proof that *your* merchant account was
   credited. Only accept a response for an order you actually put into a
   pending state for WebXPay.
3. There is no nonce, so a response can be replayed. Make your handler
   idempotent and ignore responses for orders that are already settled.
"""

import base64
import os

import frappe
from Crypto.PublicKey import RSA

from sl_payment_gateways.utils import (
	b64decode,
	clean_text,
	constant_time_equals,
	decode_utf8,
	format_amount,
	is_sandbox,
	mode_password,
	mode_value,
	validate_currency,
	validate_order_id,
)

# order_id | order_reference_number | date_time_transaction | status_code
# | comment | payment_gateway_used
RESPONSE_FIELDS = (
	"order_id",
	"order_reference_number",
	"date_time_transaction",
	"status_code",
	"comment",
	"payment_gateway_used",
)

SUCCESS_STATUS_CODES = ("0", "00")

# Separate portals, separate merchant accounts, separate key pairs.
SANDBOX_CHECKOUT_URL = "https://stagingxpay.info/index.php?route=checkout/billing"
LIVE_CHECKOUT_URL = "https://webxpay.com/index.php?route=checkout/billing"

# PKCS#1 v1.5 requires at least 8 padding bytes. Enforcing it on the way
# back out rejects short-padding forgeries rather than trusting whatever
# structure the sender chose.
MIN_PADDING_LEN = 8

# Every request in WebXPay's own published samples (php-request.txt et
# al, developers.webxpay.com/Guides/Redirect-Integration) posts this
# exact literal value in a field labelled "Mechanism" - undocumented in
# the guide's own parameter table, but present on every sample form
# right next to secret_key and payment. It looks like a fixed protocol
# constant rather than a per-merchant secret (unlike secret_key, which
# their sample also hardcodes but obviously as a fake placeholder), so
# it's used as the default here. Overridable via *_enc_method in
# WebXPay Settings in case a given account turns out to need its own.
DEFAULT_ENC_METHOD = "JCs3J+6oSz4V0LgE0zi/Bg=="


def _settings():
	try:
		return frappe.get_doc("WebXPay Settings")
	except frappe.DoesNotExistError:
		frappe.throw("WebXPay Settings doctype does not exist - create it before using WebXPay.")


def _secret_key(settings):
	"""Read the merchant secret for the active mode. Kept out of
	_settings() so that verify_response() - which is reachable by guests -
	never has to touch the credential store at all."""
	return mode_password(settings, "secret_key", is_sandbox(settings))


def _public_key(settings):
	"""Staging and production are separate WebXPay portals issuing separate
	key pairs, so the mode decides which one verifies a response."""
	pem = mode_value(settings, "public_key", is_sandbox(settings))

	try:
		return RSA.import_key(pem)
	except (ValueError, IndexError, TypeError):
		frappe.throw(
			"WebXPay Settings holds an unreadable RSA public key for %s mode."
			% ("Sandbox" if is_sandbox(settings) else "Live",)
		)


def _enc_method(settings):
	"""The `enc_method` ("Mechanism") field - see DEFAULT_ENC_METHOD.
	Unlike public_key/secret_key this isn't a credential that must be
	filled in, so it never throws: empty settings just get the sample's
	default rather than an error naming a field most accounts won't need
	to touch."""
	prefix = "sandbox_" if is_sandbox(settings) else "live_"
	value = settings.get(prefix + "enc_method") or settings.get("enc_method")
	return value.strip() if value else DEFAULT_ENC_METHOD


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
		# PS must be non-zero bytes; a zero would be read as the
		# terminator and truncate the message.
		ps += bytes(b for b in os.urandom(padding_len) if b != 0)
	ps = ps[:padding_len]

	em = b"\x00\x02" + bytes(ps) + b"\x00" + plaintext
	c = pow(_os2ip(em), e, n)
	return _i2osp(c, k)


def _rsa_public_decrypt_pkcs1_type1(ciphertext: bytes, key: RSA.RsaKey) -> bytes:
	"""PKCS#1 v1.5 block type 0x01 (signature) padding, reversed with the
	public key - equivalent to PHP's openssl_public_decrypt() verifying
	data produced by openssl_private_encrypt()."""
	n, e = key.n, key.e
	k = (n.bit_length() + 7) // 8

	if len(ciphertext) != k:
		frappe.throw("Invalid signature length for this RSA key.")

	c = _os2ip(ciphertext)
	if c >= n:
		frappe.throw("Ciphertext out of range for this RSA key.")

	em = _i2osp(pow(c, e, n), k)

	if em[0:2] != b"\x00\x01":
		frappe.throw("Invalid PKCS#1 signature padding (block type).")

	idx = 2
	while idx < len(em) and em[idx] == 0xFF:
		idx += 1

	if idx - 2 < MIN_PADDING_LEN:
		frappe.throw("Invalid PKCS#1 signature padding (too short).")

	if idx >= len(em) or em[idx] != 0x00:
		frappe.throw("Invalid PKCS#1 signature padding (terminator).")

	return em[idx + 1 :]


def build_checkout(order_id, amount, currency, customer):
	order_id = validate_order_id(order_id)
	amount = format_amount(amount)
	currency = validate_currency(currency)

	settings = _settings()
	key = _public_key(settings)
	secret_key = _secret_key(settings)

	plaintext = ("%s|%s" % (order_id, amount)).encode("utf-8")
	ciphertext = _rsa_encrypt_pkcs1_type2(plaintext, key)
	payment_field = base64.b64encode(ciphertext).decode("ascii")

	checkout_url = SANDBOX_CHECKOUT_URL if is_sandbox(settings) else LIVE_CHECKOUT_URL

	return {
		"method": "POST",
		"checkout_url": checkout_url,
		"fields": {
			# WebXPay requires an address; callers that don't collect one
			# can leave it out and get this placeholder.
			"address_line_one": clean_text(customer.get("address") or customer.get("organization"), 100, "N/A"),
			"first_name": clean_text(customer.get("first_name"), 30, "Customer"),
			"last_name": clean_text(customer.get("last_name"), 30, "-"),
			"email": clean_text(customer.get("email"), 100),
			"contact_number": clean_text(customer.get("contact_number"), 30),
			# WebXPay's integration posts this from the browser by design -
			# it identifies the merchant in the checkout form. It is still a
			# credential, which is why create_payment() is not reachable as a
			# public endpoint (see api.py).
			"secret_key": secret_key,
			"payment": payment_field,
			# See DEFAULT_ENC_METHOD - every WebXPay sample request posts
			# this field; without it, their server has failed to decrypt
			# `payment` correctly ("Invalid encryption") in practice.
			"enc_method": _enc_method(settings),
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

	payment_plaintext = decode_utf8(b64decode(payment, "payment"), "payment")
	signature_plaintext = decode_utf8(
		_rsa_public_decrypt_pkcs1_type1(b64decode(signature, "signature"), key),
		"signature",
	)

	# compare_digest rather than != : this is the check that decides
	# whether a payment is genuine, so don't leak how far it matched.
	if not constant_time_equals(signature_plaintext, payment_plaintext):
		frappe.throw("WebXPay response signature does not match payment data.")

	parts = payment_plaintext.split("|")
	if len(parts) != len(RESPONSE_FIELDS):
		# Previously a short payload zipped into a dict with missing keys
		# and fell through to status "Failed" - which reads like a declined
		# payment rather than the malformed response it actually is.
		frappe.throw(
			"Unexpected WebXPay response format: expected %d fields, got %d."
			% (len(RESPONSE_FIELDS), len(parts))
		)

	parsed = dict(zip(RESPONSE_FIELDS, parts, strict=True))

	status = "Paid" if parsed.get("status_code") in SUCCESS_STATUS_CODES else "Failed"

	return {
		"order_id": parsed.get("order_id"),
		"status": status,
		# WebXPay's response carries neither of these. Reported explicitly
		# so a caller checking `result["amount"]` sees None and knows it has
		# to verify the figure itself, instead of finding no key and
		# assuming there was nothing to check.
		"amount": None,
		"currency": None,
		# False: WebXPay signs with its own key, not a per-merchant one, and
		# the response carries no merchant id - so a valid signature does not
		# prove *our* account was credited. Callers must not settle an order
		# on this alone; see the residual risks above.
		"merchant_verified": False,
		"raw": parsed,
	}
