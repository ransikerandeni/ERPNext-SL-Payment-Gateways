# Frappe module directory for the "SL Payment Gateways" module.
#
# This looks redundant - a package with the app's own name, nested inside
# the app package - but Frappe requires it. On `bench install-app`,
# frappe/model/sync.py does:
#
#     frappe.get_module(app_name + "." + module_name)
#
# for every entry in modules.txt, where module_name is the scrubbed title
# ("SL Payment Gateways" -> "sl_payment_gateways"). Without this
# directory that import raises
#
#     ModuleNotFoundError: No module named
#     'sl_payment_gateways.sl_payment_gateways'
#
# and the install aborts. Frappe then walks this folder for doctype/,
# page/, report/ subfolders. It finds doctype/, holding this app's two
# Single DocTypes (WebXPay Settings, PayHere Settings) - see
# doctype/webxpay_settings/ and doctype/payhere_settings/.
#
# Do not delete this file - Frappe needs it to import the module even
# though nothing here is referenced directly.
