"""Controller shared by People's Bank's two Settings doctypes.

"Peoples Bank USD Settings" and "Peoples Bank LKR Settings" hold the same
fields for two different CyberSource profiles (see gateways/peoples_bank.py),
so they behave the same way and their controllers are this class, renamed.
"""

import frappe
from frappe.model.document import Document


class PeoplesBankSettingsController(Document):
	def validate(self):
		# Only worth saying for a gateway that is switched on: an Enabled one
		# is offered to payers, and every checkout they start will fail until
		# the credentials for the active mode are all there.
		if not self.get("enabled"):
			return

		prefix = "sandbox_" if self.use_sandbox else "live_"

		if not (
			self.get(prefix + "profile_id")
			and self.get(prefix + "access_key")
			and self.get_password(prefix + "secret_key", raise_exception=False)
		):
			frappe.msgprint(
				"%s Profile ID, Access Key and Secret Key are not all set - checkout will fail until they are."
				% ("Sandbox" if self.use_sandbox else "Live",),
				indicator="orange",
				alert=True,
			)

	def on_payment_request_submission(self, payment_request):
		"""Tell ERPNext not to build or email a payment URL for this request.

		Same reasoning as WebXPay/PayHere: linking a Payment Gateway
		Account is what puts the gateway's name on the accounting record,
		but it also switches on core's own checkout flow, which asks this
		controller for a payment URL to email. People's Bank's Secure
		Acceptance checkout is a signed POST form built by this app, not a
		GET redirect, so there is no URL to hand over.

		Returning False is ERPNext's documented opt-out (it is the
		`send_mail` flag in Payment Request.on_submit): the request
		submits, keeps its gateway account, and stays at `Requested` for
		the return handler to settle.
		"""
		return False
