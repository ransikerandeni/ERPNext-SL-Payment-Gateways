# PayHere — configuration and use

Covers both environments: **Sandbox** (`sandbox.payhere.lk`, for testing) and **Live** (`payhere.lk`, real money). The app holds credentials for both at once and switches between them with a single checkbox.

- [How PayHere works here](#how-payhere-works-here)
- [1. Create the Settings DocType](#1-create-the-settings-doctype)
- [2. Sandbox setup](#2-sandbox-setup)
- [3. Testing in sandbox](#3-testing-in-sandbox)
- [4. Going live](#4-going-live)
- [5. Troubleshooting](#5-troubleshooting)
- [Security notes specific to PayHere](#security-notes-specific-to-payhere)

---

## How PayHere works here

PayHere uses their **Checkout API**: you post a form with an MD5 hash, the customer pays on PayHere, and PayHere sends **two independent things back**:

```
Your site                    PayHere                        Your site
   |                            |                                |
   |  POST checkout form        |                                |
   |  (merchant_id, order_id,   |                                |
   |   amount, currency, hash)  |                                |
   |--------------------------->|                                |
   |                            |  customer pays                 |
   |                            |                                |
   |                            |  (1) server-to-server POST     |
   |                            |      to notify_url  ← THE REAL ONE
   |                            |------------------------------->|
   |                            |                                |
   |                            |  (2) browser redirect to       |
   |                            |      return_url (cosmetic)     |
   |                            |------------------------------->|
```

**`notify_url` is the authoritative one.** It is a server-to-server webhook carrying a signed payload, and it arrives whatever the customer's browser does — including if they close the tab the moment they pay. `return_url` and `cancel_url` are just where the browser lands afterwards; they carry no verifiable payload and must never be treated as proof of payment.

You supply all three URLs per request. **`notify_url` must point at your own handler — the one that settles the order.** Pointing it at `sl_payment_gateways.api.payment_return` verifies the payload and then discards it, so every payment silently goes unrecorded. (That was a real bug in an earlier version — see [SECURITY.md](../SECURITY.md) H2.)

Two environments, two entirely separate merchant accounts:

| | Sandbox | Live |
|---|---|---|
| Portal | `sandbox.payhere.lk` | `payhere.lk` |
| Checkout URL | `https://sandbox.payhere.lk/pay/checkout` | `https://www.payhere.lk/pay/checkout` |
| Merchant ID | sandbox account's | live account's |
| Merchant Secret | sandbox account's | live account's |

In PayHere's own words, sandbox *"is a completely separate deployment, so you cannot do any conversions"* to a live account. You sign up twice, and the credentials never work across environments. That is why the app stores both sets.

---

## 1. Find the Settings DocType

The app ships its own **PayHere Settings** Single DocType (`sl_payment_gateways/sl_payment_gateways/doctype/payhere_settings/`) — installing or updating the app creates it automatically, nothing to build by hand. It's already restricted to **System Manager** only, since it holds live payment credentials.

To open it: type `PayHere Settings` into the Desk awesome-bar (top search) and select it, the same way you'd reach **System Settings**. Fields:

| Label | Fieldname | Type | Notes |
|---|---|---|---|
| Use Sandbox | `use_sandbox` | Check | Default `1`. Ticked = sandbox, unticked = live |
| Sandbox Merchant ID | `sandbox_merchant_id` | Data | From `sandbox.payhere.lk` |
| Sandbox Merchant Secret | `sandbox_merchant_secret` | Password | From `sandbox.payhere.lk` |
| Live Merchant ID | `live_merchant_id` | Data | From `payhere.lk` |
| Live Merchant Secret | `live_merchant_secret` | Password | From `payhere.lk` |

> **Upgrading from an earlier version of this app that had no DocType?** If you previously created `PayHere Settings` by hand via Setup → DocType → New, `bench migrate` reconciles it with the app-owned definition above by fieldname — your existing `sandbox_merchant_id` / `live_merchant_secret` etc. values are preserved. If you were on an even older setup with plain `merchant_id` / `merchant_secret` fields only, those still work as a fallback when the mode-specific field is empty; it never reaches across modes, so an empty `live_merchant_secret` still fails with an error rather than quietly using the sandbox one.

---

## 2. Sandbox setup

**a. Create a sandbox merchant account** at <https://sandbox.payhere.lk/merchant/sign-up>. This is a separate signup from your live account.

**b. Collect the credentials.** In the sandbox dashboard, the Merchant ID is on the dashboard home; the Merchant Secret is under **Settings → Domains & Credentials**, generated per approved domain.

**c. Add your domain.** Under **Settings → Domains & Credentials**, add the domain your checkout will be posted from and wait for it to be approved. PayHere issues the Merchant Secret against that domain — a checkout posted from an unlisted domain is rejected.

**d. Fill in the Settings.** Paste into `sandbox_merchant_id` and `sandbox_merchant_secret`, leave **Use Sandbox** ticked, and save.

**e. Nothing to configure for return URLs** — unlike WebXPay, PayHere takes them per request. Your calling code supplies them:

```python
notify_url="/api/method/gateway_payment_return?gateway=PayHere",
return_url="/app/slot-allocation/%s" % order.name,
cancel_url="/app/slot-allocation/%s" % order.name,
```

All three must resolve to your own site; off-site values are rejected.

> **`notify_url` must be reachable from the public internet.** It is PayHere's server calling yours, not the browser. `localhost` will never receive it. To test on a local bench, expose it with a tunnel (ngrok, Cloudflare Tunnel) and use the tunnel host as your site URL.

---

## 3. Testing in sandbox

**Confirm the configuration first:**

```bash
bench --site <your-site> console
```

```python
from sl_payment_gateways.gateways import payhere
c = payhere.build_checkout("TEST-001", "10.00", "LKR", {
    "notify_url": "/api/method/gateway_payment_return?gateway=PayHere",
    "first_name": "Test", "email": "t@example.com",
})
print(c["checkout_url"])            # expect sandbox.payhere.lk
print(c["fields"]["merchant_id"])   # expect your SANDBOX merchant id
print(c["fields"]["notify_url"])    # expect a full https URL on your site
```

If that raises, the message names the exact field to fill and the mode it is missing for.

### Sandbox test cards

From PayHere's own Sandbox & Testing documentation. For **Name on Card, CVV and Expiry you may enter any valid data** — any future expiry, any 3-digit CVV. Cards other than these will fail.

**Successful payment:**

| Network | Number |
|---|---|
| Visa | `4916217501611292` |
| MasterCard | `5307732125531191` |
| Amex | `346781005510225` |

**Decline scenarios** — use these to check your failure handling, not just the happy path:

| Scenario | Visa | MasterCard | Amex |
|---|---|---|---|
| Insufficient funds | `4024007194349121` | `5459051433777487` | `370787711978928` |
| Limit exceeded | `4929119799365646` | `5491182243178283` | `340701811823469` |
| Do not honor | `4929768900837248` | `5388172137367973` | `374664175202812` |
| Network error | `4024007120869333` | `5237980565185003` | `373433500205887` |

No real money moves in sandbox; payments are simulated.

**What to verify before calling it done:**

- [ ] A successful card marks the order Paid — and does so via the `notify_url` webhook, not the browser redirect. Confirm by closing the tab immediately after paying: the order should still become Paid.
- [ ] Each decline card leaves the order **not** Paid.
- [ ] Cancelling at the PayHere screen leaves the order not Paid (`status_code = -1`).
- [ ] Replaying the same `notify_url` POST twice leaves the order Paid once and does not error.
- [ ] Tampering with `payhere_amount` in a replayed POST is rejected (`md5sig verification failed`).
- [ ] `Error Log` shows no `Gateway payment amount mismatch` entries for the successful run.

### Status codes

| `status_code` | Meaning | Mapped to |
|---|---|---|
| `2` | Success | `Paid` |
| `0` | Pending | `Pending` |
| `-1` | Cancelled | `Failed` |
| `-2` | Failed | `Failed` |
| `-3` | Charged back | `Failed` |

Anything unrecognised maps to `Failed` — never to `Paid`.

---

## 4. Going live

1. **Create and verify a live merchant account** at <https://payhere.lk>. PayHere requires business verification; allow time for it. Your sandbox account cannot be converted.
2. **Add and get approval for your production domain** under Settings → Domains & Credentials, then generate the live Merchant Secret against it.
3. **Fill in `live_merchant_id` and `live_merchant_secret`.** Leave the sandbox fields populated.
4. **Untick Use Sandbox** and save.
5. **Verify the switch took effect:**

   ```python
   from sl_payment_gateways.gateways import payhere
   c = payhere.build_checkout("TEST-LIVE", "10.00", "LKR",
                              {"notify_url": "/api/method/gateway_payment_return?gateway=PayHere"})
   print(c["checkout_url"])           # must print www.payhere.lk
   print(c["fields"]["merchant_id"])  # must print your LIVE merchant id
   ```

6. **Make one small real payment** and refund it from the PayHere dashboard, confirming the order reaches Paid through the webhook.
7. **Confirm `notify_url` is reachable on production** — a firewall or auth proxy in front of `/api/method/` will block PayHere's server and every payment will sit unrecorded.

**Rolling back:** tick Use Sandbox again. Both credential sets stay stored.

---

## 5. Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| `PayHere Settings doctype does not exist` | App installed but `bench migrate` never ran (rare — install-app runs it for you) | `bench --site <your-site> migrate` |
| `PayHere Settings is not configured for Live mode: set live_merchant_id` | Field empty for the active mode | Fill that exact field |
| `notify_url is required` | Caller didn't pass it | Pass your own handler's URL — see step 2e |
| `Invalid notify_url: must point at this site` | Off-site or protocol-relative URL | Use a path like `/api/method/...` or a full URL on your own host |
| `Invalid order_id` | Order name unsafe, or longer than PayHere's 50-character limit | Order names must be `[A-Za-z0-9._-/]`, max 50 for PayHere |
| `Missing required PayHere notification fields: ...` | Endpoint hit without a real webhook payload | The message names which fields were missing |
| `PayHere merchant_id mismatch` | Notification is for a different account | Nearly always a mode mismatch — a sandbox webhook arriving while Live is selected, or vice versa |
| `PayHere md5sig verification failed` | Signature doesn't match | Wrong Merchant Secret for the active mode, or the payload was tampered with. Regenerate the secret if you rotated the domain |
| `cannot be called directly over HTTP` | Something called `create_payment` as a public endpoint | Correct — route it through your own whitelisted method |

**Payment succeeded at PayHere but the order is still Pending.** The `notify_url` webhook never arrived or never landed. Check, in order:

1. Is `notify_url` publicly reachable? `curl` it from outside your network.
2. Does it point at *your* handler, not `sl_payment_gateways.api.payment_return`?
3. Is that handler `allow_guest=True`? PayHere's server has no session or CSRF token.
4. Check `Error Log` for `Gateway payment verification failed`.

**`md5sig verification failed` on every notification.** The Merchant Secret is wrong for the active mode. Sandbox and live secrets are not interchangeable — confirm you pasted the sandbox secret into `sandbox_merchant_secret` and not into the live field.

**Amount mismatch in Error Log.** `Gateway payment amount mismatch` means PayHere reported a different figure than the order's price. This is the check working; investigate before treating the order as paid.

---

## Security notes specific to PayHere

**PayHere notifications are properly bound to your account.** The `md5sig` is keyed on your merchant secret and covers merchant id, order id, amount, currency and status; the merchant id is additionally checked against your settings for the active mode. This is stronger than WebXPay, where a signed response proves nothing about which merchant was credited.

**The amount is signed — so check it.** `verify_response()` returns `amount` and `currency` from the signed payload. Compare them against what the order should cost before marking anything paid. A valid signature proves PayHere sent the message, not that the customer paid the right price.

**MD5 is PayHere's choice.** Their API defines both the checkout hash and `md5sig` this way; anything stronger simply would not verify. Nothing to fix from this side.

**Never trust `return_url`.** It is a browser redirect with no verifiable payload. A customer can navigate to it without paying. Only the `notify_url` webhook settles an order.

**Replays.** PayHere sends no nonce. Your handler must be idempotent — ignore notifications for orders that are already settled.
