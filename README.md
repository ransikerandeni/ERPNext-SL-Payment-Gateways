# SL Payment Gateways

Pluggable Sri Lankan payment gateway integrations for **ERPNext / Frappe**. Currently implemented: **WebXPay** and **PayHere**. Scaffolded and ready to extend: **People's Bank**, **Sampath Bank**, **Commercial Bank** IPGs.

## Why this exists

Frappe's built-in `payments` app covers PayPal, Stripe, Razorpay, and similar international gateways — but nothing for Sri Lankan payment gateways. Each of those requires real cryptography (RSA signing for WebXPay, MD5 hashing for PayHere) to build and verify a checkout, which **cannot run inside a Frappe Server Script** — the Server Script sandbox has no `import` capability at all (no `hashlib`, no `Crypto`, nothing beyond a small fixed set of builtins).

This app is the small piece of real, installable code that has to exist outside the sandbox to do that cryptography. It ships no DocTypes and no UI of its own — it's a thin API layer your own Server Scripts (or a custom app) call into.

## Architecture

One Python module per gateway under `sl_payment_gateways/gateways/`, each implementing the same two-function contract (see [`gateways/base.py`](sl_payment_gateways/gateways/base.py)):

```python
def build_checkout(order_id: str, amount: str, currency: str, customer: dict) -> dict:
    """Returns {"method": "POST"|"GET", "checkout_url": "...", "fields": {...}}"""

def verify_response(form_dict) -> dict:
    """Verifies a gateway's response and returns {"order_id", "status", "raw"}"""
```

[`sl_payment_gateways/api.py`](sl_payment_gateways/api.py) dispatches to whichever gateway is named, via three whitelisted methods:

- `sl_payment_gateways.api.list_gateways` — names of gateways with a real implementation (for building a "Pay with X" UI).
- `sl_payment_gateways.api.create_payment` — `(gateway, order_id, amount, currency, **customer)` → checkout details.
- `sl_payment_gateways.api.payment_return` — `(gateway)` → verifies and parses the gateway's response (reads the rest from `frappe.form_dict`).

Adding a new gateway means writing one new module and adding one line to `api.py`'s `GATEWAYS` dict — nothing else changes.

## Installation

Requires bench/Frappe (any modern version — developed and tested against Frappe 16) and Python 3.10+.

```bash
cd ~/frappe-bench   # your bench directory
bench get-app https://github.com/ransikerandeni/ERPNext-SL-Payment-Gateways.git
bench --site <your-site> install-app sl_payment_gateways
bench restart
```

That's it for the app itself — `pycryptodome` (needed for WebXPay's RSA) installs automatically as a declared dependency.

## Setup (per gateway)

The app has no settings UI of its own — each gateway module reads its credentials from a small **Single DocType** you create once via Desk (**Setup → DocType → New**, check **Is Single**). This is a deliberate design choice: it keeps the app itself free of any schema/migration surface, and every field stays visible/editable in Desk like any other setting.

### WebXPay

1. Create DocType `WebXPay Settings` (Is Single), with fields:
   - `Public Key` (Long Text)
   - `Secret Key` (Password)
   - `Use Sandbox` (Check, default 1)
2. Fill it in from your WebXPay merchant dashboard (staging: `stagingxpay.info`, production: `webxpay.com`) — **Settings → Integrations** for the Secret Key, **Settings → Integration Information → Generate keys** for the RSA Public Key.
3. In WebXPay's dashboard, under **Settings → Website Integration → Add Return URL**, set it to:
   `https://<your-site>/api/method/<your_return_endpoint>?gateway=WebXPay`
   (WebXPay only supports one fixed, sitewide return URL — see "Wiring it up" below for what `<your_return_endpoint>` should be.)

### PayHere

1. Create DocType `PayHere Settings` (Is Single), with fields:
   - `Merchant ID` (Data)
   - `Merchant Secret` (Password)
   - `Use Sandbox` (Check, default 1)
2. Fill it in from your PayHere merchant dashboard (sandbox: `sandbox.payhere.lk`, live: `payhere.lk`).

PayHere takes `notify_url`/`return_url`/`cancel_url` per request (set automatically by `gateways/payhere.py`), so there's no dashboard-side return URL to configure.

Restrict both Settings DocTypes to System Manager only — they hold live credentials.

## Wiring it up in your own app

This app deliberately doesn't touch your business logic (order validation, pricing, what "paid" means for your DocType) — that stays in your own Server Scripts or app code. A minimal integration is two endpoints:

**Start a checkout:**
```python
checkout = frappe.call(
    function="sl_payment_gateways.api.create_payment",
    gateway="WebXPay",          # or "PayHere"
    order_id="SO-00001",        # your own reference
    amount="1500.00",
    currency="LKR",
    first_name="...", last_name="...", email="...", contact_number="...",
)
# checkout = {"method": "POST", "checkout_url": "...", "fields": {...}}
# render an auto-submitting form (POST) or redirect (GET) with these
```

**Handle the return** (register this as your own whitelisted, `allow_guest=True` method — gateways call it directly with no session/CSRF token, which is safe because `verify_response()` cryptographically verifies the payload before trusting anything):
```python
@frappe.whitelist(allow_guest=True)
def my_payment_return():
    gateway = frappe.form_dict.get("gateway")
    result = frappe.call(function="sl_payment_gateways.api.payment_return", gateway=gateway)
    # result = {"order_id": "...", "status": "Paid"|"Failed"|"Pending", "raw": {...}}
    # update your own order/invoice doctype here
```

That `my_payment_return` endpoint's URL (with `?gateway=WebXPay` or `?gateway=PayHere` appended) is what goes into WebXPay's dashboard return URL / PayHere's `notify_url` above. If you're doing this as Desk-pasted Server Scripts rather than app code, an API-type Server Script with `Allow Guest` checked works the same way — see this project's worked example (a full ERPNext Slot Allocation payment flow built on this app) for a concrete pattern to copy.

## Known open items

- **WebXPay response field order is unconfirmed.** `gateways/webxpay.py`'s `verify_response` assumes `order_id|order_reference_number|date_time_transaction|status_code|comment|payment_gateway_used`, based on WebXPay's worked example on their integration guide — but their actual sample PHP file's field order wasn't fully confirmed. Verify against WebXPay's real `php-response.txt` before relying on this for live payments.
- Both `webxpay.py` and `payhere.py` send a placeholder address (`"N/A"`, or whatever `customer` dict field you pass as `organization`) — pass a real `address`/`city`/`country` through `customer` if your integration needs it.

## Adding a new gateway

1. Write `sl_payment_gateways/gateways/<name>.py` implementing `build_checkout()` and `verify_response()` per the contract in `gateways/base.py` — `webxpay.py` (RSA) and `payhere.py` (MD5 hash) are worked examples of two different crypto schemes.
2. Create a Settings DocType for its credentials, same pattern as above.
3. Register the module in `sl_payment_gateways/api.py`'s `GATEWAYS` dict, and add its name to `IMPLEMENTED` once it's working.
4. `bench update --pull` (or however you sync app updates on your bench), `bench restart`.

`gateways/peoples_bank.py`, `sampath_bank.py`, and `commercial_bank.py` are placeholder stubs following this exact pattern — deliberately not guessed implementations, since none of those banks publish public API documentation (they only share an integration spec after a signed merchant agreement). Fill them in once you have a real spec.

## Updating

```bash
cd ~/frappe-bench/apps/sl_payment_gateways
git pull
cd ~/frappe-bench
bench --site <your-site> migrate
bench restart
```

## Uninstalling

**1. Remove the app from a site** (this is what you want if you just don't need it on one particular site anymore):

```bash
bench --site <your-site> uninstall-app sl_payment_gateways
```

You'll be prompted to confirm since this is destructive to that site's use of the app. Because this app ships no DocTypes of its own, there's no data loss risk from the app's own schema — but note this does **not** touch the `WebXPay Settings` / `PayHere Settings` DocTypes you created manually via Desk (see below).

**2. Remove the app from the bench entirely** (after it's uninstalled from every site that had it):

```bash
bench remove-app sl_payment_gateways
```

This deletes `apps/sl_payment_gateways` and removes it from `sites/apps.txt`. Add `--no-backup` to skip bench's automatic pre-removal site backup if you don't need one, or `--force` to remove even if uninstall from a site failed/was skipped (only do this if you're sure no site still depends on it — check with `bench --site <your-site> list-apps` first).

**3. Clean up what the app doesn't own itself:**

Since `WebXPay Settings` and `PayHere Settings` are DocTypes *you* created via Desk (not shipped by this app), uninstalling the app leaves them behind. If you want a full clean removal:
- Delete those DocType records via **Setup → DocType**, or leave them — an orphaned Single DocType with no app behind it is harmless, just unused.
- Delete any Server Scripts / Client Scripts you pasted into Desk that called into this app's whitelisted methods (`sl_payment_gateways.api.*`) — they'll start throwing "module not found" errors once the app is removed, so remove or disable them first if you're decommissioning the integration rather than just this specific app version.

## License

MIT — see [`license.txt`](license.txt).
