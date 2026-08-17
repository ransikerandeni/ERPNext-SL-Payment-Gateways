# SL Payment Gateways

Pluggable Sri Lankan payment gateway integrations for **ERPNext / Frappe**. Currently implemented: **WebXPay** and **PayHere**. Scaffolded and ready to extend: **People's Bank**, **Sampath Bank**, **Commercial Bank** IPGs.

## Why this exists

Frappe's built-in `payments` app covers PayPal, Stripe, Razorpay, and similar international gateways — but nothing for Sri Lankan payment gateways. Each of those requires real cryptography (RSA signing for WebXPay, MD5 hashing for PayHere) to build and verify a checkout, which **cannot run inside a Frappe Server Script** — the Server Script sandbox has no `import` capability at all (no `hashlib`, no `Crypto`, nothing beyond a small fixed set of builtins).

This app is the small piece of real, installable code that has to exist outside the sandbox to do that cryptography. Beyond the two Settings DocTypes it ships for entering credentials (`WebXPay Settings`, `PayHere Settings` — see below), it has no UI of its own — it's a thin API layer your own Server Scripts (or a custom app) call into.

## Architecture

One Python module per gateway under `sl_payment_gateways/gateways/`, each implementing the same two-function contract (see [`gateways/base.py`](sl_payment_gateways/gateways/base.py)):

```python
def build_checkout(order_id: str, amount: str, currency: str, customer: dict) -> dict:
    """Returns {"method": "POST"|"GET", "checkout_url": "...", "fields": {...}}"""

def verify_response(form_dict) -> dict:
    """Verifies a response, returns
    {"order_id", "status", "amount", "currency", "merchant_verified", "raw"}"""
```

[`sl_payment_gateways/api.py`](sl_payment_gateways/api.py) dispatches to whichever gateway is named, via three whitelisted methods:

- `sl_payment_gateways.api.list_gateways` — names of gateways with a real implementation (for building a "Pay with X" UI).
- `sl_payment_gateways.api.create_payment` — `(gateway, order_id, amount, currency, **customer)` → checkout details. **Not a public endpoint**: it refuses to run when it is the method the HTTP request called, so it can only be reached *through* your own whitelisted method. See [Security model](#security-model).
- `sl_payment_gateways.api.payment_return` — `(gateway)` → verifies and parses the gateway's response (reads the rest from `frappe.form_dict`). Guest-reachable, and safe to be: it verifies cryptographically and changes no state.

Adding a new gateway means writing one new module and adding one line to `api.py`'s `GATEWAYS` dict — nothing else changes.

## Security model

Read this before wiring anything up. The short version: **this app proves a message came from the gateway. Everything else is yours to check.**

### `create_payment` must not be reachable from a browser

`create_payment()` signs a checkout for whatever `amount` it is handed — it cannot know what your orders cost — and for WebXPay it returns the merchant `secret_key` in the form fields, because WebXPay's integration posts that from the browser. If it were reachable at `/api/method/sl_payment_gateways.api.create_payment`, any logged-in user could price their own order at `1.00` and read the credential out of the response.

Frappe only lets a Server Script reach a function through `frappe.call()` if it is `@frappe.whitelist()`'d, so the decorator can't just be removed. Instead `create_payment()` checks whether it is itself the endpoint the request invoked — via Frappe's `form_dict["cmd"]`, and again via the request path as a backstop — and refuses if so. A nested call from your own whitelisted method runs with *your* method's `cmd` still in place and is allowed.

**What this means for you:** derive the amount server-side from your own records, after checking that the caller owns the order and that it isn't already paid — then call `create_payment`. Never pass an amount that came from the request.

### What `verify_response` does and does not prove

It proves the payload was signed by the gateway. It does **not** prove:

| | |
|---|---|
| the order exists, is unsettled, or was set up for this gateway | nothing here reads your records — check it yourself |
| the right amount was paid | compare `result["amount"]` / `result["currency"]` against your own price. WebXPay reports `None` — its response carries no amount, so confirm the figure in the WebXPay dashboard |
| this isn't a replay | none of these gateways sends a nonce. Make your handler idempotent and ignore responses for already-settled orders |
| your merchant account was credited | read `result["merchant_verified"]`. True (PayHere) means the signature is keyed on a secret only you hold, so it does prove this. False (WebXPay) means it does not — corroborate against local state before treating money as received |

PayHere's `md5sig` covers merchant id, order id, amount, currency and status, and the merchant id is checked against your settings — so PayHere notifications are bound to your account. WebXPay's are not.

### Input handling

`order_id`, `amount` and `currency` are validated in [`utils.py`](sl_payment_gateways/utils.py) before they reach any signing code. The pipe character is rejected in `order_id` in particular: WebXPay's payment string is `order_id|amount`, so a pipe would let the caller control the amount field the gateway parses. Amounts go through `Decimal` with `ROUND_HALF_UP`, never `float`. Signature comparisons are constant-time.

## Installation

Requires bench/Frappe (any modern version — developed and tested against Frappe 16) and Python 3.10+.

```bash
cd ~/frappe-bench   # your bench directory
bench get-app https://github.com/ransikerandeni/ERPNext-SL-Payment-Gateways.git
bench --site <your-site> install-app sl_payment_gateways
bench restart
```

That's it for the app itself — `pycryptodome` (needed for WebXPay's RSA) installs automatically as a declared dependency.

Confirm it landed:

```bash
bench --site <your-site> list-apps | grep sl_payment_gateways
bench --site <your-site> console -c "import sl_payment_gateways, Crypto; print('app + pycryptodome OK')"
```

### A note on the app layout

`sl_payment_gateways/sl_payment_gateways/` is a real, required directory, not a stray copy. On install, Frappe reads `modules.txt`, scrubs each entry (`"SL Payment Gateways"` → `sl_payment_gateways`) and imports `<app>.<module>` as a package. Without that folder, `bench install-app` fails with:

```
No module named 'sl_payment_gateways.sl_payment_gateways'
```

Under it sits `doctype/`, holding this app's two Single DocTypes — `webxpay_settings/` and `payhere_settings/` — which is what Frappe walks this folder to find (along with `page/` and `report/` subfolders, of which this app has none). Don't delete this folder for looking unused: `git status` will show it's not empty.

## Setup (per gateway)

**Full step-by-step guides, covering sandbox and live for each gateway:**

| Gateway | Guide |
|---|---|
| WebXPay | **[docs/webxpay.md](docs/webxpay.md)** |
| PayHere | **[docs/payhere.md](docs/payhere.md)** |

Each covers creating the Settings DocType, getting credentials from the right portal, running test payments (including PayHere's sandbox test cards), switching to live, and troubleshooting every error this app can raise. Start there — the summary below is orientation only.

The app has no settings UI of its own: each gateway reads its credentials from a small **Single DocType** you create once via Desk (**Setup → DocType → New**, check **Is Single**). This keeps the app free of any schema/migration surface, and every field stays visible/editable in Desk like any other setting.

### Sandbox and live are separate accounts

Both gateways treat their test and production environments as **entirely separate merchant accounts** — PayHere's sandbox is a separate deployment that cannot be converted to a live account, and WebXPay's staging portal issues its own RSA key pair. So each Settings DocType holds **both** credential sets, and a `use_sandbox` checkbox selects which is used:

```
use_sandbox = 1  →  sandbox_*  fields  →  the gateway's test portal
use_sandbox = 0  →  live_*     fields  →  the gateway's production portal
```

| DocType | Sandbox fields | Live fields | Switch |
|---|---|---|---|
| `WebXPay Settings` | `sandbox_public_key` (Long Text), `sandbox_secret_key` (Password) | `live_public_key`, `live_secret_key` | `use_sandbox` (Check) |
| `PayHere Settings` | `sandbox_merchant_id` (Data), `sandbox_merchant_secret` (Password) | `live_merchant_id`, `live_merchant_secret` | `use_sandbox` (Check) |

A missing credential fails loudly and names the exact field and mode — it never falls back to the other environment's:

```
PayHere Settings is not configured for Live mode: set `live_merchant_id`.
```

Configurations from before this split (plain `public_key` / `merchant_id` / …) keep working: the unprefixed field is used when the mode-specific one is empty.

**Restrict both Settings DocTypes to System Manager only** — they hold live credentials.

## Wiring it up in your own app

This app deliberately doesn't touch your business logic (order validation, pricing, what "paid" means for your DocType) — that stays in your own Server Scripts or app code. A minimal integration is two endpoints:

**Start a checkout** — note that every check happens *before* the call, and the amount is read from your own records, never from the request:
```python
@frappe.whitelist()
def my_create_payment():
    order = frappe.get_doc("My Order", frappe.form_dict.get("order"))

    # Your rules: who may pay for this, is it still unpaid, what does it cost.
    if order.owner != frappe.session.user:
        frappe.throw("You can only pay for your own order.")
    if order.payment_status == "Paid":
        frappe.throw("Already paid.")

    amount = "%.2f" % order.grand_total     # from the database, not the caller

    return frappe.call(
        function="sl_payment_gateways.api.create_payment",
        gateway=frappe.form_dict.get("gateway"),   # "WebXPay" or "PayHere"
        order_id=order.name,
        amount=amount,
        currency="LKR",
        first_name="...", last_name="...", email="...", contact_number="...",
        # PayHere reads these per request; all three must be on this site.
        notify_url="/api/method/my_app.api.my_payment_return?gateway=PayHere",
        return_url="/app/my-order/%s" % order.name,
        cancel_url="/app/my-order/%s" % order.name,
        items="Registration fee (%s)" % order.name,
    )
# returns {"method": "POST", "checkout_url": "...", "fields": {...}}
# render an auto-submitting form (POST) or redirect (GET) with these
```

`notify_url` is **required** for PayHere and must point at your own handler — the one that actually settles the order. Pointing it at `sl_payment_gateways.api.payment_return` verifies the payload and then discards it, leaving every payment unrecorded.

**Handle the return** (register this as your own whitelisted, `allow_guest=True` method — gateways call it directly with no session/CSRF token, which is safe because `verify_response()` cryptographically verifies the payload before trusting anything):
```python
@frappe.whitelist(allow_guest=True)
def my_payment_return():
    gateway = frappe.form_dict.get("gateway")
    result = frappe.call(function="sl_payment_gateways.api.payment_return", gateway=gateway)
    # result = {"order_id", "status": "Paid"|"Failed"|"Pending", "amount", "currency", "raw"}

    order = frappe.get_doc("My Order", result["order_id"])

    if order.payment_status == "Paid":
        return                      # replay of an already-settled order

    status = result["status"]

    # `amount` is None for WebXPay - its response carries no amount to check.
    if status == "Paid" and result["amount"]:
        if result["amount"] != ("%.2f" % order.grand_total) or result["currency"] != "LKR":
            frappe.log_error(title="Payment amount mismatch", message=str(result))
            status = "Failed"

    order.db_set("payment_status", status)
```

The [`payment/gateway/`](../payment/gateway/) scripts in this project are a working version of exactly this pair.

That `my_payment_return` endpoint's URL (with `?gateway=WebXPay` or `?gateway=PayHere` appended) is what goes into WebXPay's dashboard return URL / PayHere's `notify_url` above. If you're doing this as Desk-pasted Server Scripts rather than app code, an API-type Server Script with `Allow Guest` checked works the same way — see this project's worked example (a full ERPNext Slot Allocation payment flow built on this app) for a concrete pattern to copy.

## Tests

The suite stubs Frappe ([`tests/frappe_stub.py`](tests/frappe_stub.py)), so it runs with plain `pytest` — no bench, site or database:

```bash
pip install -e ".[dev]" && pytest
```

The Client Script driving this app has its own dependency-free harness, since a pasted-into-Desk script can otherwise only be tested by clicking through a browser:

```bash
node ../slot_allocation/client_script.test.js
```

415 tests covering the RSA/PKCS#1 implementation against a real keypair, PayHere's hash scheme against an independently written reference, tampering and forgery attempts on both, sandbox/live credential selection (including that a sandbox payload is rejected in live mode and vice versa), and seeded fuzzing that asserts hostile input can never come back as `Paid` and never escapes as an unhandled exception.

## Known open items

- ~~WebXPay response field order is unconfirmed.~~ **Resolved.** WebXPay's Redirect Integration guide documents the decoded response as `order_id|order_reference_number|date_time_transaction|status_code|comment|payment_gateway_used`, with status `0`/`00` = approved and `15` = declined — which is what `gateways/webxpay.py` implements. A response with a different field count is now rejected outright rather than parsed as a decline.
- Both `webxpay.py` and `payhere.py` send a placeholder address (`"N/A"`, or whatever `customer` dict field you pass as `organization`) — pass a real `address`/`city`/`country` through `customer` if your integration needs it.

## Adding a new gateway

1. Write `sl_payment_gateways/gateways/<name>.py` implementing `build_checkout()` and `verify_response()` per the contract in `gateways/base.py` — `webxpay.py` (RSA) and `payhere.py` (MD5 hash) are worked examples of two different crypto schemes.
2. Create a Settings DocType for its credentials, same pattern as above — with `sandbox_*` and `live_*` field pairs plus `use_sandbox`, read via `utils.mode_value()` / `utils.mode_password()`.
3. Register the module in `sl_payment_gateways/api.py`'s `GATEWAYS` dict, and add its name to `IMPLEMENTED` once it's working.
4. `bench update --pull` (or however you sync app updates on your bench), `bench restart`.

`gateways/peoples_bank.py`, `sampath_bank.py`, and `commercial_bank.py` are placeholder stubs following this exact pattern — deliberately not guessed implementations, since none of those banks publish public API documentation (they only share an integration spec after a signed merchant agreement). Fill them in once you have a real spec.

## Updating

On whatever machine runs your bench (not necessarily where you edit this repo — see below if they're different):

```bash
cd ~/frappe-bench/apps/sl_payment_gateways
git pull
cd ~/frappe-bench
bench --site <your-site> migrate
bench build --app sl_payment_gateways
bench restart
```

- `bench migrate` is the step that matters most: it's what applies any new/changed DocTypes (e.g. `WebXPay Settings`, `PayHere Settings`) to your site's database. Skipping it leaves the site running old schema even though the code updated.
- `bench build --app sl_payment_gateways` regenerates static assets, scoped to just this app rather than a full-bench rebuild (add `--force` to ignore the build cache). This app has no JS/CSS of its own, so there's nothing new to bundle — running it is harmless and matches the standard update sequence, but for this app specifically `bench migrate` is the step that actually does something.
- `bench restart` reloads the Python workers so the new code actually gets served. Only needed under supervisor/production setups — `bench start` (dev mode) picks up changes automatically.

**If you edit this repo somewhere other than the bench server** (e.g. a laptop, with the server pulling from GitHub): push your changes to the remote first (`git push origin main`), then run the `git pull` above on the server. If the server has no direct GitHub access, pull locally and `rsync`/`scp` the app directory across instead.

## Uninstalling

**1. Remove the app from a site** (this is what you want if you just don't need it on one particular site anymore):

```bash
bench --site <your-site> uninstall-app sl_payment_gateways
```

You'll be prompted to confirm since this is destructive to that site's use of the app. This **does** remove the `WebXPay Settings` / `PayHere Settings` DocTypes and the credentials stored in them — back up that data first (`bench --site <your-site> backup`) if you might reinstall later, since uninstalling and reinstalling gives you a blank Settings doctype, not your old values.

**2. Remove the app from the bench entirely** (after it's uninstalled from every site that had it):

```bash
bench remove-app sl_payment_gateways
```

This deletes `apps/sl_payment_gateways` and removes it from `sites/apps.txt`. Add `--no-backup` to skip bench's automatic pre-removal site backup if you don't need one, or `--force` to remove even if uninstall from a site failed/was skipped (only do this if you're sure no site still depends on it — check with `bench --site <your-site> list-apps` first).

**3. Clean up what the app doesn't own itself:**

`WebXPay Settings` / `PayHere Settings` and their stored credentials go with the app (see step 1). What's *not* app-owned, and so isn't touched by uninstall, is anything you pasted into Desk yourself: delete or disable any Server Scripts / Client Scripts that called this app's whitelisted methods (`sl_payment_gateways.api.*`) before removing the app — they'll start throwing "module not found" errors otherwise, if you're decommissioning the integration rather than just this specific app version.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [`license.txt`](license.txt).
