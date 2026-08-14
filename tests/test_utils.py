import pytest

import frappe
from sl_payment_gateways import utils


class TestOrderId:
	@pytest.mark.parametrize("value", ["SO-0001", "SLOT.2026.0001", "a", "A/B_C-1", "x" * 100])
	def test_accepts_realistic_names(self, value):
		assert utils.validate_order_id(value) == value

	@pytest.mark.parametrize(
		"value",
		[
			"SO-0001|99999.00",  # would shift WebXPay's amount field
			"SO-0001\nX",
			"SO-0001\r\nX",
			"SO 0001",  # space breaks the delimited payload
			"SO-0001<script>",
			"SO#0001",
			"",
			"   ",
			"x" * 101,
			None,
		],
	)
	def test_rejects_unsafe_names(self, value):
		with pytest.raises(frappe.ValidationError):
			utils.validate_order_id(value)

	def test_rejects_over_gateway_specific_limit(self):
		# 60 chars is fine generally but too long for PayHere's 50.
		value = "S" * 60
		assert utils.validate_order_id(value) == value
		with pytest.raises(frappe.ValidationError):
			utils.validate_order_id(value, max_length=50)


class TestAmount:
	@pytest.mark.parametrize(
		("given", "expected"),
		[
			("1500", "1500.00"),
			("1500.5", "1500.50"),
			(1500, "1500.00"),
			(1500.5, "1500.50"),
			("  1500.00  ", "1500.00"),
			("0.01", "0.01"),
			("1500.005", "1500.01"),  # ROUND_HALF_UP, not banker's rounding
			("1500.015", "1500.02"),
		],
	)
	def test_normalises_to_two_decimals(self, given, expected):
		assert utils.format_amount(given) == expected

	def test_half_up_differs_from_python_default(self):
		# round(1500.005, 2) and "%.2f" both give 1500.00 here; money
		# rounding should go up. This is the case that would have made a
		# signed hash disagree with the invoice.
		assert utils.format_amount("2.675") == "2.68"

	@pytest.mark.parametrize("value", ["0", "-1", "-0.01", "nan", "inf", "-inf", "abc", "", None, "1e400"])
	def test_rejects_nonsense(self, value):
		with pytest.raises(frappe.ValidationError):
			utils.format_amount(value)

	def test_rejects_absurd_amount(self):
		with pytest.raises(frappe.ValidationError):
			utils.format_amount("100000001.00")

	def test_scientific_notation_is_normalised_not_echoed(self):
		# Decimal accepts 1.5E3; it must come out as a plain string a
		# gateway will parse the same way we hashed it.
		assert utils.format_amount("1.5E3") == "1500.00"


class TestCurrency:
	@pytest.mark.parametrize(("given", "expected"), [("LKR", "LKR"), ("usd", "USD"), (" lkr ", "LKR")])
	def test_accepts_iso_codes(self, given, expected):
		assert utils.validate_currency(given) == expected

	@pytest.mark.parametrize("value", ["", None, "LKRR", "LK", "L K", "LKR|X", "123"])
	def test_rejects_anything_else(self, value):
		with pytest.raises(frappe.ValidationError):
			utils.validate_currency(value)


class TestCleanText:
	def test_truncates(self):
		assert utils.clean_text("x" * 100, 30) == "x" * 30

	def test_strips_control_characters(self):
		assert utils.clean_text("Bad\r\nName\x00", 50) == "BadName"

	def test_falls_back_to_default_when_empty(self):
		assert utils.clean_text(None, 30, "Customer") == "Customer"
		assert utils.clean_text("   ", 30, "Customer") == "Customer"
		assert utils.clean_text("\r\n", 30, "Customer") == "Customer"

	def test_keeps_ordinary_text(self):
		assert utils.clean_text("  Ransike Randeni ", 30) == "Ransike Randeni"


class TestB64Decode:
	def test_round_trip(self):
		assert utils.b64decode("aGVsbG8=", "payment") == b"hello"

	@pytest.mark.parametrize("value", ["not base64!!", "aGVsbG8", "aGVs bG8=", ""])
	def test_rejects_malformed(self, value):
		with pytest.raises(frappe.ValidationError):
			utils.b64decode(value, "payment")

	def test_strict_mode_rejects_smuggled_characters(self):
		# Without validate=True this decodes fine, silently dropping the
		# stray characters - a mangled signature that looks well-formed.
		with pytest.raises(frappe.ValidationError):
			utils.b64decode("aGVs*bG8=", "signature")


class TestSiteUrl:
	def test_resolves_relative_path(self):
		assert utils.site_url("/app/order/SO-1", "return_url") == "https://erp.example.com/app/order/SO-1"

	def test_accepts_absolute_url_on_this_site(self):
		url = "https://erp.example.com/api/method/x"
		assert utils.site_url(url, "notify_url") == url

	@pytest.mark.parametrize(
		"value",
		[
			"https://evil.example.net/collect",
			"//evil.example.net/collect",  # protocol-relative
			"http://erp.example.com.evil.net/",
			"javascript:alert(1)",
			"data:text/html,x",
			"app/order/1",  # relative but not rooted
			"https://erp.example.com/x\r\nSet-Cookie: a=b",
			"",
			None,
		],
	)
	def test_rejects_off_site_and_malformed(self, value):
		with pytest.raises(frappe.ValidationError):
			utils.site_url(value, "notify_url")
