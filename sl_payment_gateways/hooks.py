# Frappe reads hooks.app_version off this module, so the rebind is the
# point even though nothing here references it.
from . import __version__ as app_version  # noqa: F401

app_name = "sl_payment_gateways"
app_title = "SL Payment Gateways"
app_publisher = "Ransike Randeni"
app_description = "Pluggable Sri Lankan payment gateway integrations for ERPNext (WebXPay, PayHere, and more)"
app_email = "ransikerandeni@gmail.com"
app_license = "MIT"
app_logo_url = "/assets/sl_payment_gateways/images/app-logo.png"

# This app is mostly pure Python utility code (RSA/hash signing for
# gateway checkouts) exposed as whitelisted methods - no fixtures, no
# doc_events, no scheduled jobs. It does ship two Single DocTypes
# ("WebXPay Settings", "PayHere Settings" - see
# sl_payment_gateways/doctype/) that Frappe creates automatically on
# `bench install-app` / `bench migrate`, so credentials can be entered
# from Desk instead of a site_config.json edit. Nothing else needed
# here.
