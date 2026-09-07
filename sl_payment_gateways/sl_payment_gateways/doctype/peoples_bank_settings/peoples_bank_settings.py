import frappe
from frappe.model.document import Document


class PeoplesBankSettings(Document):
	def validate(self):
		if not self.use_sandbox and not (
			self.live_profile_id
			and self.live_access_key
			and self.get_password("live_secret_key", raise_exception=False)
		):
			frappe.msgprint(
				"Live Profile ID, Access Key and Secret Key are not all set - checkout will fail until they are.",
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
