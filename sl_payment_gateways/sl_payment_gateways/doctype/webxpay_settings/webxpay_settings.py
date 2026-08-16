import frappe
from frappe.model.document import Document


class WebXPaySettings(Document):
	def validate(self):
		if not self.use_sandbox and not (self.live_public_key and self.get_password("live_secret_key", raise_exception=False)):
			frappe.msgprint(
				"Live Public Key and Live Secret Key are empty - checkout will fail until both are set.",
				indicator="orange",
				alert=True,
			)
