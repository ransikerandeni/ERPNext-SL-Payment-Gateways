import frappe
from frappe.model.document import Document


class PayHereSettings(Document):
	def validate(self):
		if not self.use_sandbox and not (
			self.live_merchant_id and self.get_password("live_merchant_secret", raise_exception=False)
		):
			frappe.msgprint(
				"Live Merchant ID and Live Merchant Secret are empty - checkout will fail until both are set.",
				indicator="orange",
				alert=True,
			)

	def on_payment_request_submission(self, payment_request):
		"""Tell ERPNext not to build or email a payment URL for this request.

		Linking a Payment Gateway Account to a Payment Request is what puts
		the gateway's name on the accounting record - but it also switches
		on core's own checkout flow: on_submit() asks this controller for a
		payment URL and emails it out. That flow assumes a gateway you can
		reach with a GET redirect, and PayHere is a signed POST form built
		by this app, so there is no URL to hand over; letting core try would
		fail on a controller that has no get_payment_url() to call.

		Returning False is ERPNext's documented way to opt out (it is the
		`send_mail` flag in Payment Request.on_submit): the request submits,
		keeps its gateway account, and stays at `Requested` for the return
		handler to settle. The checkout itself stays where it already is,
		in the caller's own Server Script.
		"""
		return False
