"""Shared validation/normalisation helpers used by every gateway module.

Everything that reaches a gateway module ultimately came from an HTTP
request somewhere, so it gets validated here once rather than being
re-checked (or forgotten) per gateway. The rules are deliberately strict:
a payment string is a delimited, signed record, and anything that can
smuggle a delimiter, a newline or a non-finite number into it is a way to
change what the merchant ends up being told they were paid.
"""

import base64
import hmac
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlparse

import frappe

# Every gateway here builds a pipe-delimited payment string, and several
# put the order id straight into a URL. Keep it to characters that can't
# shift a field boundary, break a header, or need escaping to be safe.
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9._\-/]{1,100}$")

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Sanity ceiling so a typo (or a tampered caller) can't sign a checkout
# for an absurd amount. Well above any realistic LKR registration fee.
MAX_AMOUNT = Decimal("100000000.00")


def validate_order_id(order_id, max_length=100):
	"""Return `order_id` as a str, or throw if it can't be safely embedded.

	Rejects the pipe character in particular: WebXPay's payment string is
	`order_id|amount`, so an order id containing a pipe would let the
	caller control the amount field the gateway parses.
	"""
	if order_id is None:
		frappe.throw("order_id is required.")

	order_id = str(order_id).strip()

	if not ORDER_ID_RE.match(order_id):
		frappe.throw(
			"Invalid order_id: it must be 1-100 characters of letters, digits, "
			"dot, underscore, hyphen or slash."
		)

	if len(order_id) > max_length:
		frappe.throw("order_id is too long for this gateway (max %d characters)." % (max_length,))

	return order_id


def format_amount(amount):
	"""Normalise a money value to a plain `123.45` string.

	Uses Decimal rather than float: gateway signatures are computed over
	the *string* form of the amount, so a float's rounding surprise here
	becomes a signature mismatch (or a silently wrong charge) later.
	"""
	if amount is None:
		frappe.throw("amount is required.")

	try:
		value = Decimal(str(amount).strip())
	except (InvalidOperation, ValueError):
		frappe.throw("Invalid amount: %s" % (frappe.utils.escape_html(str(amount)),))

	if not value.is_finite():
		frappe.throw("Invalid amount: must be a finite number.")

	if value <= 0:
		frappe.throw("Invalid amount: must be greater than zero.")

	if value > MAX_AMOUNT:
		frappe.throw("Invalid amount: exceeds the maximum allowed (%s)." % (MAX_AMOUNT,))

	# ROUND_HALF_UP, not Python's default banker's rounding - matches what
	# an accounting system (and the customer) expects of money.
	return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_currency(currency):
	"""Return an ISO-4217-shaped currency code, or throw."""
	if not currency:
		frappe.throw("currency is required.")

	currency = str(currency).strip().upper()

	if not CURRENCY_RE.match(currency):
		frappe.throw("Invalid currency: expected a 3-letter ISO code.")

	return currency


def clean_text(value, max_length, default=""):
	"""Trim a free-text customer field to something a form post can carry.

	Strips control characters (CR/LF especially - these end up in POST
	bodies and, for some gateways, in URLs) and truncates.
	"""
	if value is None:
		value = default

	value = "".join(ch for ch in str(value) if ch == " " or ch.isprintable()).strip()

	if not value:
		value = default

	return value[:max_length]


def b64decode(value, label):
	"""Strict base64 decode with an error the caller can act on.

	`validate=True` matters: without it Python silently discards
	non-alphabet characters, so a mangled signature can decode to
	something that merely *looks* well-formed. An empty string is valid
	base64 for empty bytes, which is never a legitimate payment or
	signature, so it is rejected here too.
	"""
	if not value:
		frappe.throw("Malformed %s: empty." % (label,))

	try:
		return base64.b64decode(str(value), validate=True)
	except Exception:
		frappe.throw("Malformed %s: not valid base64." % (label,))


def decode_utf8(raw, label):
	try:
		return raw.decode("utf-8")
	except UnicodeDecodeError:
		frappe.throw("Malformed %s: not valid UTF-8." % (label,))


def constant_time_equals(a, b):
	"""Timing-safe comparison of two text values.

	hmac.compare_digest raises TypeError when handed a str containing
	non-ASCII - and every value being compared here arrived in an HTTP
	request, so "attacker puts an emoji in md5sig" would otherwise be an
	unhandled 500 with a traceback on a guest-reachable endpoint.
	Comparing the UTF-8 encodings sidesteps that without weakening the
	comparison.
	"""
	return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def site_url(url, label):
	"""Resolve `url` against this site, rejecting anything off-site.

	Return/cancel URLs are where the customer's browser is sent after
	paying, so an off-site value is an open redirect wearing the site's
	name; a notify URL is where the *authoritative* payment webhook goes,
	so an off-site value silently diverts payment confirmations.
	"""
	if not url:
		frappe.throw("%s is required." % (label,))

	url = str(url).strip()

	if "\n" in url or "\r" in url:
		frappe.throw("Invalid %s: contains a line break." % (label,))

	parsed = urlparse(url)

	# Relative path - resolve it against the site and we're done.
	if not parsed.scheme and not parsed.netloc:
		if not url.startswith("/"):
			frappe.throw("Invalid %s: must be an absolute path or a URL on this site." % (label,))
		return frappe.utils.get_url(url)

	if parsed.scheme not in ("http", "https"):
		frappe.throw("Invalid %s: only http(s) URLs are allowed." % (label,))

	site = urlparse(frappe.utils.get_url())
	if parsed.netloc.lower() != site.netloc.lower():
		# Note: `//evil.com/x` parses with an empty scheme and a non-empty
		# netloc, so protocol-relative URLs land here too.
		frappe.throw("Invalid %s: must point at this site (%s)." % (label, site.netloc))

	return url
