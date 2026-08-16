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
