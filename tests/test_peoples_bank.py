"""People's Bank IPG - CyberSource Secure Acceptance Hosted Checkout.

The signature is a plain HMAC-SHA256 over an ordered `name=value` list,
so these tests check it against an independently written reference
implementation rather than against the app's own helper: an error shared
by both would otherwise pass unnoticed.

The response fixtures follow the real payloads in the bank's integration
pack (`ResponseParams Success.txt`, `ResponseParams Fail.txt`).
"""

import base64
import hashlib
import hmac

import pytest

import frappe
from sl_payment_gateways.gateways import peoples_bank

from .conftest import (
	PEOPLES_LIVE_PROFILE_ID,
	PEOPLES_SANDBOX_ACCESS_KEY,
	PEOPLES_SANDBOX_PROFILE_ID,
	PEOPLES_SANDBOX_SECRET,
)

RECEIPT = "/api/method/my_app.api.payment_return?gateway=Peoples%20Bank"


def reference_signature(fields, names, secret=PEOPLES_SANDBOX_SECRET):
	"""CyberSource's signing scheme, written out longhand.

	Deliberately not built from peoples_bank's helpers - this is the
	independent check on them.
	"""
	pairs = []
	for name in names:
		pairs.append(name + "=" + str(fields.get(name, "")))
	data = ",".join(pairs).encode("utf-8")

	return base64.b64encode(hmac.new(secret.encode("utf-8"), data, hashlib.sha256).digest()).decode()


class TestBuildCheckout:
	def test_posts_to_the_cybersource_test_host_in_sandbox(self, peoples_bank_settings):
		result = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})

		assert result["method"] == "POST"
		assert result["checkout_url"] == peoples_bank.SANDBOX_CHECKOUT_URL

	def test_carries_every_field_secure_acceptance_requires(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		# The guide's "Required Signed Fields" list, in full.
		for required in (
			"access_key",
			"amount",
			"currency",
			"locale",
			"profile_id",
			"reference_number",
			"signed_date_time",
			"signed_field_names",
			"transaction_type",
			"transaction_uuid",
		):
			assert fields[required], required

		assert fields["reference_number"] == "SO-0001"
		assert fields["amount"] == "1500.00"
		assert fields["currency"] == "LKR"
		assert fields["transaction_type"] == "sale"
		assert fields["profile_id"] == PEOPLES_SANDBOX_PROFILE_ID
		assert fields["access_key"] == PEOPLES_SANDBOX_ACCESS_KEY

	def test_signature_matches_an_independent_implementation(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		names = fields["signed_field_names"].split(",")

		assert fields["signature"] == reference_signature(fields, names)

	def test_every_name_in_signed_field_names_is_actually_present(self, peoples_bank_settings):
		# A signed name with no field signs an empty value here and a
		# different one at CyberSource, which rejects the whole request.
		fields = peoples_bank.build_checkout(
			"SO-0001", "1500.00", "LKR", {"return_url": "/paid", "notify_url": RECEIPT}
		)["fields"]

		for name in fields["signed_field_names"].split(","):
			assert name in fields, name

	def test_unsigned_field_names_covers_the_billing_details(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		unsigned = fields["unsigned_field_names"].split(",")

		assert "bill_to_forename" in unsigned
		# Nothing that decides what is charged may be left unsigned.
		for money in ("amount", "currency", "reference_number", "transaction_type", "profile_id"):
			assert money not in unsigned

	def test_signed_and_unsigned_lists_do_not_overlap(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		signed = set(fields["signed_field_names"].split(","))
		unsigned = set(fields["unsigned_field_names"].split(","))

		assert not signed & unsigned

	def test_signed_date_time_is_utc_in_the_documented_format(self, peoples_bank_settings):
		import re

		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fields["signed_date_time"])

	def test_transaction_uuid_is_unique_per_attempt(self, peoples_bank_settings):
		# CyberSource uses it to detect duplicate submissions, so a reused
		# value turns a genuine retry into a rejected transaction.
		first = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]
		second = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		assert first["transaction_uuid"] != second["transaction_uuid"]

	def test_amount_is_normalised_before_signing(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500", "lkr", {})["fields"]

		assert fields["amount"] == "1500.00"
		assert fields["currency"] == "LKR"

	def test_order_id_longer_than_cybersources_limit_is_refused(self, peoples_bank_settings):
		# reference_number is String(50); a truncated one would settle
		# against an order that does not exist.
		with pytest.raises(frappe.ValidationError, match="too long"):
			peoples_bank.build_checkout("S" * 51, "1500.00", "LKR", {})

	def test_customer_details_are_passed_through(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout(
			"SO-0001",
			"1500.00",
			"LKR",
			{
				"first_name": "Ransike",
				"last_name": "Randeni",
				"email": "ransike@example.com",
				"city": "Colombo",
				"country": "LK",
			},
		)["fields"]

		assert fields["bill_to_forename"] == "Ransike"
		assert fields["bill_to_surname"] == "Randeni"
		assert fields["bill_to_email"] == "ransike@example.com"

	@pytest.mark.parametrize(
		("country", "expected"),
		[("LK", "LK"), ("lk", "LK"), (" us ", "US"), ("Sri Lanka", "LK"), ("", "LK"), (None, "LK"), (7, "LK")],
	)
	def test_country_is_a_two_letter_code_or_the_default(
		self, peoples_bank_settings, country, expected
	):
		# bill_to_address_country is String(2): truncating "Sri Lanka" to
		# "Sr" would have CyberSource reject the whole request.
		# state/postal supplied throughout: a non-LK country without them
		# is refused outright, which is TestOptionalBillingFields' subject.
		fields = peoples_bank.build_checkout(
			"SO-0001",
			"1500.00",
			"LKR",
			{"country": country, "state": "CA", "postal_code": "94105"},
		)["fields"]

		assert fields["bill_to_address_country"] == expected

	def test_missing_customer_details_get_the_sample_packs_placeholders(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		assert fields["bill_to_forename"] == "Customer"
		assert fields["bill_to_email"] == "null@cybersource.com"
		assert fields["bill_to_address_country"] == "LK"


class TestOptionalBillingFields:
	"""State and postal code are dropped rather than sent empty.

	CyberSource reads a field named in unsigned_field_names but posted
	with an empty value as supplied-and-invalid, and declines the whole
	transaction (reason_code 102) - which is what a People's Bank test
	payment did until these were omitted.
	"""

	def test_dropped_entirely_when_the_caller_has_no_value(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		unsigned = fields["unsigned_field_names"].split(",")

		for name in ("bill_to_address_state", "bill_to_address_postal_code"):
			assert name not in fields
			assert name not in unsigned

	def test_whitespace_only_values_count_as_absent(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout(
			"SO-0001", "1500.00", "LKR", {"state": "   ", "postal_code": "\t"}
		)["fields"]

		assert "bill_to_address_state" not in fields
		assert "bill_to_address_postal_code" not in fields

	def test_sent_and_named_when_present(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout(
			"SO-0001", "1500.00", "LKR", {"state": "Western", "postal_code": "00700"}
		)["fields"]

		unsigned = fields["unsigned_field_names"].split(",")

		assert fields["bill_to_address_state"] == "Western"
		assert fields["bill_to_address_postal_code"] == "00700"
		assert "bill_to_address_state" in unsigned
		assert "bill_to_address_postal_code" in unsigned

	@pytest.mark.parametrize(
		"customer",
		[
			{"country": "US"},
			{"country": "US", "state": "CA"},
			{"country": "US", "postal_code": "94105"},
		],
	)
	def test_non_lk_billing_country_requires_both(self, peoples_bank_settings, customer):
		# The bank's own rule. Failing here beats building a hosted page
		# the payer can only be declined on.
		with pytest.raises(frappe.ValidationError):
			peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", customer)

	def test_lk_billing_country_does_not_require_them(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {"country": "LK"})["fields"]

		assert fields["bill_to_address_country"] == "LK"


class TestOverrideUrls:
	def test_absent_when_the_caller_supplies_none(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		for field in peoples_bank.OVERRIDE_FIELDS:
			assert field not in fields
			assert field not in fields["signed_field_names"]

	def test_present_and_signed_when_supplied(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout(
			"SO-0001",
			"1500.00",
			"LKR",
			{"return_url": "/paid", "cancel_url": "/cancelled", "notify_url": RECEIPT},
		)["fields"]

		names = fields["signed_field_names"].split(",")

		# The guide calls out override_custom_receipt_page in particular as
		# a field that must be signed to prevent tampering.
		for field in peoples_bank.OVERRIDE_FIELDS:
			assert field in names

		assert fields["signature"] == reference_signature(fields, names)

	def test_are_resolved_against_this_site(self, peoples_bank_settings):
		fields = peoples_bank.build_checkout(
			"SO-0001", "1500.00", "LKR", {"return_url": "/paid"}
		)["fields"]

		assert fields["override_custom_receipt_page"] == "https://erp.example.com/paid"

	def test_off_site_urls_are_refused(self, peoples_bank_settings):
		# An off-site receipt page is an open redirect; an off-site
		# back-office POST silently diverts payment confirmations.
		for key in ("return_url", "cancel_url", "notify_url"):
			with pytest.raises(frappe.ValidationError, match="must point at this site"):
				peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {key: "https://evil.example/x"})

	def test_http_urls_are_refused(self, peoples_bank_settings, monkeypatch):
		# CyberSource requires https for these; an http one fails later, as
		# a receipt the payer never reaches.
		monkeypatch.setattr(frappe.utils, "get_url", lambda path="": "http://erp.example.com" + str(path))

		with pytest.raises(frappe.ValidationError, match="requires an https"):
			peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {"return_url": "/paid"})


class TestCheckoutUrlOverride:
	def test_defaults_to_cybersource_per_mode(self, peoples_bank_settings):
		assert peoples_bank._checkout_url(peoples_bank_settings) == peoples_bank.SANDBOX_CHECKOUT_URL

		peoples_bank_settings["use_sandbox"] = 0
		assert peoples_bank._checkout_url(peoples_bank_settings) == peoples_bank.LIVE_CHECKOUT_URL

	def test_a_bank_hosted_page_can_be_configured(self, peoples_bank_settings):
		# Some merchants are fronted by the bank's own QR middle page,
		# which takes the same signed fields at a different URL.
		bank_page = "https://egateway.peoplesbank.lk/ipg_qr_middle_page_uat/index.php"
		peoples_bank_settings["sandbox_checkout_url"] = bank_page

		result = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})

		assert result["checkout_url"] == bank_page

	@pytest.mark.parametrize("url", ["http://egateway.peoplesbank.lk/x", "not a url", "javascript:alert(1)"])
	def test_a_non_https_override_is_refused(self, peoples_bank_settings, url):
		peoples_bank_settings["sandbox_checkout_url"] = url

		with pytest.raises(frappe.ValidationError, match="invalid `sandbox_checkout_url`"):
			peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})


class TestVerifyResponse:
	def test_accepts_a_genuine_approval(self, peoples_bank_settings, peoples_bank_response):
		result = peoples_bank.verify_response(peoples_bank_response())

		assert result["order_id"] == "SO-0001"
		assert result["status"] == "Paid"
		assert result["amount"] == "1500.00"
		assert result["currency"] == "LKR"

	def test_reports_the_authorised_amount_in_preference_to_the_requested_one(
		self, peoples_bank_settings, peoples_bank_response
	):
		# auth_amount is what was actually authorised; req_amount is only
		# what we asked for.
		result = peoples_bank.verify_response(
			peoples_bank_response(amount="1500.00", auth_amount="1500.00")
		)

		assert result["amount"] == "1500.00"

	@pytest.mark.parametrize(
		("decision", "status"),
		[
			("ACCEPT", "Paid"),
			("REVIEW", "Pending"),
			("DECLINE", "Failed"),
			("CANCEL", "Failed"),
			("ERROR", "Failed"),
			("SOMETHING_NEW", "Failed"),
			("", "Failed"),
		],
	)
	def test_maps_every_documented_decision(
		self, peoples_bank_settings, peoples_bank_response, decision, status
	):
		# REVIEW in particular: the authorisation was declined and a
		# capture might still be possible, so the money is not in hand.
		result = peoples_bank.verify_response(peoples_bank_response(decision=decision))

		assert result["status"] == status

	def test_decision_case_and_padding_do_not_matter(self, peoples_bank_settings, peoples_bank_response):
		assert peoples_bank.verify_response(peoples_bank_response(decision=" accept "))["status"] == "Paid"

	def test_merchant_verified_is_true(self, peoples_bank_settings, peoples_bank_response):
		# The HMAC key belongs to our profile alone, unlike WebXPay's
		# shared signing key.
		assert peoples_bank.verify_response(peoples_bank_response())["merchant_verified"] is True

	def test_a_tampered_amount_is_rejected(self, peoples_bank_settings, peoples_bank_response):
		payload = peoples_bank_response(amount="1500.00")
		payload["req_amount"] = "1.00"

		with pytest.raises(frappe.ValidationError, match="signature verification failed"):
			peoples_bank.verify_response(payload)

	def test_a_tampered_decision_is_rejected(self, peoples_bank_settings, peoples_bank_response):
		payload = peoples_bank_response(decision="DECLINE")
		payload["decision"] = "ACCEPT"

		with pytest.raises(frappe.ValidationError, match="signature verification failed"):
			peoples_bank.verify_response(payload)

	def test_a_response_signed_with_the_wrong_secret_is_rejected(
		self, peoples_bank_settings, peoples_bank_response
	):
		with pytest.raises(frappe.ValidationError, match="signature verification failed"):
			peoples_bank.verify_response(peoples_bank_response(secret="not-our-secret-key"))

	def test_a_response_for_another_merchant_profile_is_rejected(
		self, peoples_bank_settings, peoples_bank_response
	):
		# Correctly signed by *someone*, but not for our profile - so it is
		# not evidence that our account was credited.
		payload = peoples_bank_response(profile_id=PEOPLES_LIVE_PROFILE_ID)

		with pytest.raises(frappe.ValidationError, match="different merchant profile"):
			peoples_bank.verify_response(payload)

	def test_missing_signature_is_a_clean_error(self, peoples_bank_settings, peoples_bank_response):
		payload = peoples_bank_response()
		del payload["signature"]

		with pytest.raises(frappe.ValidationError, match="Missing signature"):
			peoples_bank.verify_response(payload)

	@pytest.mark.parametrize("value", [None, "", "   "])
	def test_missing_signed_field_names_is_a_clean_error(self, peoples_bank_settings, value):
		with pytest.raises(frappe.ValidationError, match="Missing signed_field_names"):
			peoples_bank.verify_response(frappe._dict({"signature": "x", "signed_field_names": value}))

	def test_an_empty_entry_in_signed_field_names_is_rejected(self, peoples_bank_settings):
		with pytest.raises(frappe.ValidationError, match="empty entry"):
			peoples_bank.verify_response(
				frappe._dict({"signature": "x", "signed_field_names": "decision,,req_amount"})
			)

	def test_an_absurd_signed_field_list_is_rejected(self, peoples_bank_settings):
		with pytest.raises(frappe.ValidationError, match="too many fields"):
			peoples_bank.verify_response(
				frappe._dict({"signature": "x", "signed_field_names": ",".join(["f"] * 1000)})
			)


class TestOnlySignedFieldsAreTrusted:
	"""CyberSource signs every field it sends, so anything outside the
	signed set was added by whoever posted to us. Their guide's own
	instruction is to ignore it."""

	def test_an_unsigned_decision_cannot_make_a_decline_look_paid(
		self, peoples_bank_settings, peoples_bank_response
	):
		# `decision` left out of the signed set entirely, then supplied
		# unsigned - the classic version of this attack.
		payload = peoples_bank_response(
			decision="DECLINE",
			signed_field_names=["req_reference_number", "req_amount", "req_currency", "signed_field_names"],
		)
		payload["decision"] = "ACCEPT"

		with pytest.raises(frappe.ValidationError, match="decision is not in the signed fields"):
			peoples_bank.verify_response(payload)

	def test_an_unsigned_reference_number_is_refused(self, peoples_bank_settings, peoples_bank_response):
		# Otherwise a genuine response could be re-pointed at someone
		# else's order.
		payload = peoples_bank_response(
			signed_field_names=["decision", "req_amount", "req_currency", "signed_field_names"]
		)
		payload["req_reference_number"] = "SO-9999"

		with pytest.raises(frappe.ValidationError, match="req_reference_number is not in the signed fields"):
			peoples_bank.verify_response(payload)

	def test_unsigned_extras_never_reach_the_result(self, peoples_bank_settings, peoples_bank_response):
		payload = peoples_bank_response(auth_amount="1500.00", injected="whatever")

		result = peoples_bank.verify_response(payload)

		assert "injected" not in result["raw"]
		assert set(result["raw"]) <= set(payload["signed_field_names"].split(","))

	def test_an_unsigned_amount_cannot_overstate_the_payment(
		self, peoples_bank_settings, peoples_bank_response
	):
		# auth_amount outside the signed set must be ignored, not read as
		# the captured figure.
		payload = peoples_bank_response(amount="1500.00")
		payload["auth_amount"] = "999999.00"

		assert peoples_bank.verify_response(payload)["amount"] == "1500.00"


class TestSettingsErrors:
	def test_a_missing_settings_doctype_says_so(self):
		with pytest.raises(frappe.ValidationError, match="does not exist"):
			peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})

	def test_a_missing_credential_names_the_field_and_mode(self, peoples_bank_settings):
		peoples_bank_settings["use_sandbox"] = 0
		peoples_bank_settings["live_profile_id"] = ""

		with pytest.raises(frappe.ValidationError, match="Live mode: set `live_profile_id`"):
			peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})

	def test_the_unprefixed_legacy_fields_still_work(
		self, peoples_bank_legacy_settings, peoples_bank_response
	):
		fields = peoples_bank.build_checkout("SO-0001", "1500.00", "LKR", {})["fields"]

		assert fields["profile_id"] == PEOPLES_SANDBOX_PROFILE_ID
		assert peoples_bank.verify_response(peoples_bank_response())["status"] == "Paid"
