from . import __version__ as app_version

app_name = "sl_payment_gateways"
app_title = "SL Payment Gateways"
app_publisher = "Ransike Randeni"
app_description = "Pluggable Sri Lankan payment gateway integrations for ERPNext (WebXPay, PayHere, and more)"
app_email = "ransikerandeni@gmail.com"
app_license = "MIT"

# This app is pure Python utility code (RSA/hash signing for gateway
# checkouts) exposed as whitelisted methods - no DocTypes, no fixtures,
# no doc_events, no scheduled jobs. Nothing else needed here.
