# WebXPay — configuration and use

Covers both environments: **Sandbox** (WebXPay's staging portal, for testing) and **Live** (production, real money). The app holds credentials for both at once and switches between them with a single checkbox.

- [How WebXPay works here](#how-webxpay-works-here)
- [1. Create the Settings DocType](#1-create-the-settings-doctype)
- [2. Sandbox setup](#2-sandbox-setup)
- [3. Testing in sandbox](#3-testing-in-sandbox)
- [4. Going live](#4-going-live)
- [5. Troubleshooting](#5-troubleshooting)
- [Security notes specific to WebXPay](#security-notes-specific-to-webxpay)

---

## How WebXPay works here

WebXPay uses a **redirect integration**. Your site builds an RSA-encrypted `payment` blob, posts a form to WebXPay's checkout page, the customer pays there, and WebXPay redirects back to a fixed return URL with an RSA-signed response.

```
Your site                     WebXPay                       Your site
   |                             |                              |
   |  POST checkout form         |                              |
   |  (payment = RSA(order|amt), |                              |
   |   secret_key, customer)     |                              |
   |---------------------------->|                              |
   |                             |  customer enters card        |
   |                             |                              |
   |                             |  POST payment + signature    |
   |                             |----------------------------->|
   |                                                            |
   |                              verify_response() checks the signature
   |                              then YOUR handler settles the order
```

Two environments, two entirely separate merchant accounts:

| | Sandbox (staging) | Live (production) |
|---|---|---|
| Portal | `stagingxpay.info` | `webxpay.com` |
| Checkout URL | `https://stagingxpay.info/index.php?route=checkout/billing` | `https://webxpay.com/index.php?route=checkout/billing` |
| Public key | staging key pair | production key pair |
| Secret key | staging secret | production secret |

**The key pairs are different.** A response signed by staging will not verify against the production public key, and vice versa — which is deliberate, and is what stops a staging transaction being replayed at your live site. This is why the app stores both sets rather than one set you overwrite.

---

## 1. Create the Settings DocType

This app ships no DocTypes, so you create one once: **Setup → DocType → New**, name it exactly `WebXPay Settings`, tick **Is Single**, and set **Module** to any module on your site.

Add these fields:

| Label | Fieldname | Type | Notes |
|---|---|---|---|
| Use Sandbox | `use_sandbox` | Check | Default `1`. Ticked = staging, unticked = live |
| Sandbox Public Key | `sandbox_public_key` | Long Text | PEM from the staging portal |
| Sandbox Secret Key | `sandbox_secret_key` | Password | From the staging portal |
| Live Public Key | `live_public_key` | Long Text | PEM from the production portal |
| Live Secret Key | `live_secret_key` | Password | From the production portal |

> **Fieldnames matter, labels don't.** Frappe derives the fieldname from the label, so confirm each one in the field's Fieldname box before saving.

Then **restrict the DocType to System Manager only** (Role Permissions Manager → remove every other role). It holds live payment credentials.

> **Upgrading from an earlier version of this app?** If you already have plain `public_key` / `secret_key` fields, they still work — the app falls back to them when the mode-specific field is empty. Add the four new fields when you're ready to keep both environments configured at once. The fallback never reaches across modes: an empty `live_public_key` fails with an error, it does not quietly use the sandbox key.

---

## 2. Sandbox setup

**a. Get a staging account.** WebXPay issues staging access when you onboard as a merchant — contact your WebXPay account manager if you only have production credentials. Staging is a separate portal at `stagingxpay.info` with its own login.

**b. Collect the credentials.** In the staging dashboard:

- **Secret Key** — Settings → Integrations
- **Public Key** — Settings → Integration Information → *Generate keys*

Copy the public key as the full PEM block, including the header and footer lines:

```
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
-----END PUBLIC KEY-----
```

> WebXPay's own guidance: make sure there are **no spaces inside the secret key** when you paste it. A stray space produces a checkout that fails with an unhelpful error on their side.

**c. Fill in the Settings.** Paste into `sandbox_public_key` and `sandbox_secret_key`, leave **Use Sandbox** ticked, and save.

**d. Set the return URL.** In the staging dashboard: **Settings → Website Integration → Add Return URL**:

```
https://<your-site>/api/method/<your_return_endpoint>?gateway=WebXPay
```

For this project that endpoint is `gateway_payment_return`:

```
https://<your-site>/api/method/gateway_payment_return?gateway=WebXPay
```

> **WebXPay supports only one return URL per account, site-wide.** It is not per-request. This is the main practical reason to keep staging and production as separate accounts — one can point at your test site and the other at production. If you need to test against a local bench, expose it with a tunnel (ngrok, Cloudflare Tunnel) and point the *staging* return URL at the tunnel.

---

## 3. Testing in sandbox

**Confirm the configuration first** without touching the UI — this prints the mode and target URL, and fails loudly if a credential is missing:

```bash
bench --site <your-site> console
```

```python
from sl_payment_gateways.gateways import webxpay
c = webxpay.build_checkout("TEST-001", "10.00", "LKR", {"first_name": "Test", "email": "t@example.com"})
print(c["checkout_url"])          # expect stagingxpay.info
print(sorted(c["fields"]))        # expect the full field list, no exception
```

If that raises, read the message — it names the exact field to fill and the mode it is missing for.

**Then run a real test payment** through your own UI. A successful round trip looks like:

1. Your order goes to `payment_status = Pending` and the browser posts to `stagingxpay.info`.
2. You pay on WebXPay's staging checkout.
3. WebXPay redirects to your return URL, `verify_response()` passes, and your handler sets `payment_status = Paid`.

**Test cards:** WebXPay does not publish staging card numbers. Request them from your WebXPay account manager along with staging access — do not guess, and do not use a real card against staging.

**What to verify before calling it done:**

- [ ] A successful payment marks the order Paid.
- [ ] A cancelled/declined payment does **not** mark it Paid (status code `15` is a decline; `0`/`00` is approved).
- [ ] Replaying the same return URL POST twice leaves the order Paid exactly once and does not error.
- [ ] Paying an order that is already Paid is rejected before a checkout is ever built.
- [ ] `Error Log` in Desk shows no `Gateway payment verification failed` entries for the successful run.

---

## 4. Going live

Do these in order. The switch itself is one checkbox, but everything else must be in place first.

1. **Get production credentials.** Log in at `webxpay.com` → Settings → Integrations (secret key) and Settings → Integration Information → Generate keys (public key).
2. **Fill in `live_public_key` and `live_secret_key`.** Leave the sandbox fields populated — you will want them again.
3. **Set the production return URL** in the production dashboard: Settings → Website Integration → Add Return URL, pointing at your production site. Remember it is one fixed URL per account.
4. **Untick Use Sandbox** and save.
5. **Verify the switch took effect:**

   ```python
   from sl_payment_gateways.gateways import webxpay
   print(webxpay.build_checkout("TEST-LIVE", "10.00", "LKR", {})["checkout_url"])
   # must print webxpay.com, NOT stagingxpay.info
   ```

6. **Make one small real payment** and refund it from the WebXPay dashboard. This is the only way to confirm the production key pair, the return URL and your handler all line up — staging cannot prove it.
7. **Reconcile the amount** against the WebXPay dashboard. See the security note below on why this matters for WebXPay specifically.

**Rolling back:** tick Use Sandbox again. Both credential sets stay stored, so nothing needs re-entering.

---

## 5. Troubleshooting

Errors raised by this app, and what each one means:

| Message | Cause | Fix |
|---|---|---|
| `WebXPay Settings doctype does not exist` | Step 1 not done | Create the Single DocType |
| `WebXPay Settings is not configured for Sandbox mode: set sandbox_public_key` | Field empty for the active mode | Fill that exact field. The message always names the mode and the field |
| `WebXPay Settings holds an unreadable RSA public key for Live mode` | Key isn't valid PEM | Re-copy the whole block including `-----BEGIN/END PUBLIC KEY-----` |
| `Invalid order_id` | Order name has a space, pipe, `#`, or other unsafe character | Order names must be `[A-Za-z0-9._-/]`, max 100 chars |
| `Invalid amount` | Zero, negative, non-numeric, or over 100,000,000 | Check what your pricing code passed |
| `Missing payment or signature` | Return URL was hit without a WebXPay payload | Usually someone opening the URL directly — harmless |
| `Malformed payment: not valid base64` | Truncated or mangled response | Check for a proxy rewriting the POST body |
| `WebXPay response signature does not match payment data` | Signature is not from the key configured for the active mode | Almost always a mode mismatch — staging response arriving while Live is selected, or vice versa |
| `Invalid PKCS#1 signature padding` | Response is not a WebXPay signature at all | Someone probing the endpoint |
| `Unexpected WebXPay response format: expected 6 fields, got N` | WebXPay changed their response layout | Compare against your downloaded `php-response.txt` and open an issue |
| `cannot be called directly over HTTP` | Something called `create_payment` as a public endpoint | Correct — route it through your own whitelisted method instead |

**Payment succeeded at WebXPay but the order is still Pending.** The return URL in the dashboard is wrong, or points at the wrong environment. Check `Error Log` for `Gateway payment verification failed`.

**Everything worked in sandbox and fails live.** Check `use_sandbox` is actually unticked *and* that the production return URL is set in the production dashboard — it is a separate account, so the staging URL you configured does not carry over.

---

## Security notes specific to WebXPay

**The response carries no amount.** `verify_response()` returns `amount: None` for WebXPay. There is nothing in the payload to check the price against, so a WebXPay "Paid" proves only that WebXPay says that order reached that status. Reconcile figures against the WebXPay dashboard; do not treat the callback as evidence of how much was paid.

**The response carries no merchant identifier**, and WebXPay signs with its own key rather than a per-merchant one. Any WebXPay merchant could therefore produce a validly signed success for an arbitrary `order_id` and post it to your return URL. Your handler must only accept a response for an order it actually put into a pending state for WebXPay — which is what `create_gateway_payment` sets up and `gateway_payment_return` checks.

**The `secret_key` is posted from the browser.** That is WebXPay's design, not a bug here. It is why `create_payment` refuses to be a public endpoint — see [SECURITY.md](../SECURITY.md) C1/C2.

**Never point a live return URL at a staging account or the reverse.** With separate key pairs the signature check fails safely, but you will lose the payment record.
