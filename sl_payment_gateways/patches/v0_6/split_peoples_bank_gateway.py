"""Split People's Bank into a USD gateway and an LKR gateway.

People's Bank asked for one CyberSource profile per currency, so the single
"Peoples Bank" gateway becomes "Peoples Bank (USD)" - the existing profile -
and a new "Peoples Bank (LKR)". This patch:

  1. moves everything in the old "Peoples Bank Settings" (sandbox and live
     credentials, secret keys included) into "Peoples Bank USD Settings",
     and ticks Enabled there so the site keeps taking payments exactly as
     it did - the old gateway was always on;
  2. renames the ERPNext Payment Gateway record "Peoples Bank" to
     "Peoples Bank (USD)" and points it at the new Settings doctype
     (rename_doc carries the Payment Gateway Accounts that link to it);
  3. creates a "Peoples Bank (LKR)" Payment Gateway record beside it, when
     the site uses Payment Gateway records at all.

The LKR Settings are left empty and switched off: those credentials are a
different profile, which only the bank can issue.

Runs post_model_sync, so both new doctypes exist, and before migrate's
remove_orphan_doctypes() deletes the old one along with its values. Safe to
run again: values already in the USD Settings are never overwritten.
"""

import frappe
from frappe.utils.password import delete_all_passwords_for, get_decrypted_password, set_encrypted_password

OLD_SETTINGS = "Peoples Bank Settings"
USD_SETTINGS = "Peoples Bank USD Settings"
LKR_SETTINGS = "Peoples Bank LKR Settings"

OLD_GATEWAY = "Peoples Bank"
USD_GATEWAY = "Peoples Bank (USD)"
LKR_GATEWAY = "Peoples Bank (LKR)"

# Everything the old doctype held except `allow_lkr`, which has no meaning now
# that LKR is a profile of its own.
VALUE_FIELDS = (
	"use_sandbox",
	"use_test_billing_data",
	"sandbox_profile_id",
	"sandbox_access_key",
	"sandbox_checkout_url",
	"live_profile_id",
	"live_access_key",
	"live_checkout_url",
)

PASSWORD_FIELDS = ("sandbox_secret_key", "live_secret_key")


def execute():
	_move_settings()
	_rename_payment_gateway()
	_create_lkr_payment_gateway()
	_drop_old_settings()


def _move_settings():
	old = frappe.db.get_singles_dict(OLD_SETTINGS) or {}
	secrets = {
		fieldname: get_decrypted_password(OLD_SETTINGS, OLD_SETTINGS, fieldname, raise_exception=False)
		for fieldname in PASSWORD_FIELDS
	}

	if not old and not any(secrets.values()):
		# A fresh install, or already moved and dropped.
		return

	# Already configured (an earlier run, or somebody typed them in by hand):
	# leave it alone rather than overwrite a newer value with an older one.
	if frappe.db.get_single_value(USD_SETTINGS, "sandbox_profile_id") or frappe.db.get_single_value(
		USD_SETTINGS, "live_profile_id"
	):
		return

	for fieldname in VALUE_FIELDS:
		if old.get(fieldname) not in (None, ""):
			frappe.db.set_single_value(USD_SETTINGS, fieldname, old[fieldname])

	for fieldname, secret in secrets.items():
		if secret:
			set_encrypted_password(USD_SETTINGS, USD_SETTINGS, secret, fieldname)
			# What Frappe itself leaves in tabSingles for a saved Password field,
			# so the form shows the key as set rather than blank.
			frappe.db.set_single_value(USD_SETTINGS, fieldname, "*" * len(secret))

	frappe.db.set_single_value(USD_SETTINGS, "enabled", 1)


def _rename_payment_gateway():
	if not frappe.db.exists("DocType", "Payment Gateway"):
		return

	if frappe.db.exists("Payment Gateway", OLD_GATEWAY) and not frappe.db.exists("Payment Gateway", USD_GATEWAY):
		frappe.rename_doc("Payment Gateway", OLD_GATEWAY, USD_GATEWAY, force=True, show_alert=False)

	if frappe.db.exists("Payment Gateway", USD_GATEWAY):
		frappe.db.set_value("Payment Gateway", USD_GATEWAY, "gateway_settings", USD_SETTINGS)


def _create_lkr_payment_gateway():
	# Only where the site already records People's Bank as a Payment Gateway: a
	# site that never linked one has chosen not to, and gets nothing new.
	if not frappe.db.exists("DocType", "Payment Gateway") or not frappe.db.exists("Payment Gateway", USD_GATEWAY):
		return

	if frappe.db.exists("Payment Gateway", LKR_GATEWAY):
		return

	frappe.get_doc(
		{"doctype": "Payment Gateway", "gateway": LKR_GATEWAY, "gateway_settings": LKR_SETTINGS}
	).insert(ignore_permissions=True)


def _drop_old_settings():
	# remove_orphan_doctypes() would delete the DocType anyway, but leaves its
	# encrypted keys behind in __Auth. Gone here, now that they have been copied.
	delete_all_passwords_for(OLD_SETTINGS, OLD_SETTINGS)
	frappe.db.delete("Singles", {"doctype": OLD_SETTINGS})

	if frappe.db.exists("DocType", OLD_SETTINGS):
		frappe.delete_doc("DocType", OLD_SETTINGS, force=True, ignore_missing=True)
