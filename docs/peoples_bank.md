# People's Bank IPG — configuration and use

Covers both environments: **Sandbox** (CyberSource's test host, for testing) and **Live** (real money). The app holds credentials for both at once and switches between them with a single checkbox.

- [How People's Bank works here](#how-peoples-bank-works-here)
- [Two gateways, one per currency](#two-gateways-one-per-currency)
- [1. Find the Settings DocTypes](#1-find-the-settings-doctypes)
- [2. Sandbox setup](#2-sandbox-setup)
- [3. Testing in sandbox](#3-testing-in-sandbox)
- [4. Going live](#4-going-live)
- [5. Troubleshooting](#5-troubleshooting)
- [Security notes specific to People's Bank](#security-notes-specific-to-peoples-bank)

---

## How People's Bank works here

People's Bank does not run a payment protocol of its own. Their **IPG** is a branded **CyberSource (Visa Acceptance) Secure Acceptance Hosted Checkout** merchant profile: the bank issues you a *profile ID*, an *access key* and a *secret key*, and the protocol itself is CyberSource's, documented in their **Secure Acceptance Hosted Checkout Integration** guide — a copy of which ships in the bank's integration pack, alongside their PHP sample (`CYBSPEBBasic.php`).

The scheme is an HMAC-SHA256 signature over an **ordered** list of fields:

```
signature = base64( HMAC-SHA256( secret_key,
              "name1=value1,name2=value2,..." ) )
```

…where the names, and their order, are exactly what the `signed_field_names` field lists. The same construction is used in both directions.

```
Your site                     CyberSource / People's Bank        Your site
   |                                    |                            |
   |  POST signed checkout form         |                            |
   |  (profile_id, access_key, amount,  |                            |
   |   currency, reference_number,      |                            |
   |   signed_field_names, signature)   |                            |
   |----------------------------------->|                            |
   |                                    |  customer enters card      |
   |                                    |  and pays on their page    |
   |                                    |                            |
   |                                    |  (1) POST to the receipt   |
   |                                    |      page, via the browser |
   |                                    |--------------------------->|
   |                                    |                            |
   |                                    |  (2) back-office POST,     |
   |                                    |      server-to-server      |
   |                                    |--------------------------->|
```

Both callbacks carry the **same signed payload**, so either one is verifiable — unlike PayHere, where only the webhook is. The back-office POST is still worth configuring, because it arrives whatever the customer's browser does.

You supply all three URLs per request, and each is sent as the matching `override_*` field:

| `customer` key | CyberSource field | What it is |
|---|---|---|
| `return_url` | `override_custom_receipt_page` | Where the browser lands, carrying the signed response |
| `cancel_url` | `override_custom_cancel_page` | Where "cancel" on the hosted page goes |
| `notify_url` | `override_backoffice_post_url` | Server-to-server notification |

All three must resolve to your own site, and **must be HTTPS** — CyberSource requires TLS 1.2 or later on these, so this app refuses an `http://` one up front rather than letting it fail later as a receipt page the payer never reaches. All three are optional: omit them and the profile's own configured pages are used.

Two environments, two entirely separate merchant profiles:

| | Sandbox | Live |
|---|---|---|
| Endpoint | `testsecureacceptance.cybersource.com/pay` | `secureacceptance.cybersource.com/pay` |
| Profile ID | test profile from the bank | production profile from the bank |
| Access key / secret key | issued per profile | issued per profile |

---

## Two gateways, one per currency

People's Bank issues a **separate CyberSource profile for each currency**, so this app has two gateways:

| Gateway | Charges in | Settings DocType |
|---|---|---|
| **Peoples Bank (USD)** | USD only | `Peoples Bank USD Settings` |
| **Peoples Bank (LKR)** | LKR only | `Peoples Bank LKR Settings` |

Each gateway has its own profile ID, access key and secret key, for both Sandbox and Live. A profile asked to sign the other currency refuses before signing anything. If it didn't, CyberSource would reject the checkout at its hosted page with `decision=ERROR reason_code=102 invalid_fields=currency`, after the payer had already got there.

**Which one a payer sees depends on the fee.** Front ends call `sl_payment_gateways.api.list_gateways(currency=<fee currency>)`:

| Fee priced in | USD gateway | LKR gateway | Offered |
|---|---|---|---|
| USD | Enabled | (any) | **Peoples Bank (USD)**, no conversion |
| LKR | (any) | Enabled | **Peoples Bank (LKR)**, no conversion |
| LKR | Enabled | **Off** | Peoples Bank (USD), with the fee converted to USD at the maintained rate (as before the split) |
| USD | **Off** | Enabled | Peoples Bank (LKR), with the fee converted to LKR |

> **Upgrading from 0.5.x.** The old `Peoples Bank` gateway becomes `Peoples Bank (USD)`. On `bench migrate`, the patch copies its credentials into `Peoples Bank USD Settings` and ticks **Enabled** there. It also renames the `Payment Gateway` record and creates a `Peoples Bank (LKR)` record beside it. Checkouts started before the upgrade come back with `?gateway=Peoples Bank`, which still verifies, as USD.

---

## 1. Find the Settings DocTypes

The app ships two Single DocTypes, **Peoples Bank USD Settings** and **Peoples Bank LKR Settings** (`sl_payment_gateways/sl_payment_gateways/doctype/peoples_bank_usd_settings/` and `…/peoples_bank_lkr_settings/`). Installing or updating the app creates them automatically. Both are restricted to **System Manager**, since they hold live payment credentials.

To open one, type its name into the Desk awesome-bar. Both have the same fields:

| Label | Fieldname | Type | Notes |
|---|---|---|---|
| Enabled | `enabled` | Check | Default `0`. Ticked = offered to payers and allowed to sign checkouts. Responses for payments already made are verified either way |
| Use Sandbox | `use_sandbox` | Check | Default `1`. Ticked = CyberSource test host, unticked = live |
| Sandbox Profile ID | `sandbox_profile_id` | Data | A UUID, e.g. `3DEEB7C4-3568-4CDF-8247-1A15527CD3B2` |
| Sandbox Access Key | `sandbox_access_key` | Data | 32 hex characters |
| Sandbox Secret Key | `sandbox_secret_key` | Password | Long hex string — the HMAC key |
| Sandbox Checkout URL | `sandbox_checkout_url` | Data | Optional, see below |
| Use CyberSource Test Billing Data | `use_test_billing_data` | Check | Default `0`. Sandbox only — see below |
| Live Profile ID | `live_profile_id` | Data | A **different** profile from the test one |
| Live Access Key | `live_access_key` | Data | |
| Live Secret Key | `live_secret_key` | Password | |
| Live Checkout URL | `live_checkout_url` | Data | Optional, see below |

**Never put the USD profile's credentials into the LKR Settings, or the reverse.** The bank issues each profile for one currency, so the other currency would fail at CyberSource.

**The two Checkout URL fields are almost always left blank.** Blank means "post to CyberSource's own endpoint for this mode", which is what a standard Secure Acceptance profile wants. Set one only if People's Bank has put you behind their **own** hosted front end — their QR "middle page" at `egateway.peoplesbank.lk`, which takes the same signed fields at a different URL. Whatever you set must be `https://`.

> The access key is **not** a secret: it is posted from the browser as part of the checkout form, by design. The **secret key is** — it is the HMAC key, and anyone holding it can sign a payment for your profile. It never leaves the server.

---

## 2. Sandbox setup

Do this once per gateway: once for USD, and again for LKR with the LKR profile's credentials.

**a. Get test credentials from People's Bank.** They come from your bank contact, not from a self-service signup: a test Profile ID, Access Key and Secret Key **for each currency's profile**, issued against their CyberSource test account. Their integration pack (`cybs guide.docx`) is where these are listed.

**b. Fill in the Settings.** Open that currency's Settings (`Peoples Bank USD Settings` or `Peoples Bank LKR Settings`). Paste into `sandbox_profile_id`, `sandbox_access_key` and `sandbox_secret_key`, leave **Use Sandbox** ticked, tick **Enabled**, and save.

**c. Ask the bank for a Business Center login.** Their pack includes a *CYBS Portal User Guide*; the login is what you use to search transactions and confirm what actually happened, and it is the source of truth when a response and your records disagree.

**d. Configure the profile's response pages, or override them per request.** Secure Acceptance will not activate a profile until a customer response page is configured. Either set them once in the Business Center (**Payment Configuration → Secure Acceptance Settings → your profile → Customer Response**), or pass `return_url` / `cancel_url` / `notify_url` on every `create_payment` call and let the `override_*` fields take precedence:

```python
notify_url="/api/method/gateway_payment_return?gateway=Peoples Bank (LKR)",
return_url="/api/method/gateway_payment_return?gateway=Peoples Bank (LKR)",
cancel_url="/app/slot-allocation/%s" % order.name,
```

> **Your site must be served over HTTPS**, and `notify_url` must be reachable from the public internet — it is CyberSource's server calling yours, not the browser. To test on a local bench, expose it with a tunnel (ngrok, Cloudflare Tunnel) and set the site's `host_name` to the tunnel's https URL.

---

## 3. Testing in sandbox

**Confirm the configuration first:**

```bash
bench --site <your-site> console
```

```python
from sl_payment_gateways.gateways import peoples_bank_lkr      # or peoples_bank_usd, with "USD"
result = peoples_bank_lkr.build_checkout("TEST-001", "10.00", "LKR", {})
print(result["checkout_url"])          # ...testsecureacceptance.cybersource.com/pay
print(result["fields"]["profile_id"])  # your test profile
print(result["fields"]["signature"])   # non-empty
```

Then run a real test payment through your own checkout page. Card numbers and test scenarios come from CyberSource's test-guide links in the bank's pack; `4111 1111 1111 1111` with any future expiry is the standard Visa test card. Confirm the result in the Business Center's transaction search as well as in your own records.

**If a sandbox payment declines and you suspect the billing address**, tick
**Use CyberSource Test Billing Data**. The test environment's fraud and AVS
rules are tuned for CyberSource's own dummy address, so a real Sri Lankan one
can be declined there for reasons that would never apply live — and their
sandbox activation mail asks you to use the dummy set when you are not testing
with real data. Ticking it replaces the payer's name, email and full billing
address with:

```
bill_to_forename            noreal
bill_to_surname             name
bill_to_email               null@cybersource.com
bill_to_address_line1       1295 Charleston Rd
bill_to_address_city        Mountain View
bill_to_address_state       CA
bill_to_address_country     US
bill_to_address_postal_code 94043
```

It is ignored entirely unless **Use Sandbox** is also ticked, so a checkbox
left on cannot bill a live card to a Mountain View address. Untick it before
testing the billing address path itself — with it on, your own prompt and
whatever it collects are never exercised.

### Decision values

The response's `decision` field is what this app maps to a status. From CyberSource's *Types of Notifications* table:

| `decision` | Meaning | This app returns |
|---|---|---|
| `ACCEPT` | Successful transaction (reason codes 100, 110) | `Paid` |
| `REVIEW` | Authorisation declined; a capture **might** still be possible | `Pending` |
| `DECLINE` | Declined by the processor or by fraud settings | `Failed` |
| `CANCEL` | The customer backed out | `Failed` |
| `ERROR` | Access denied, page not found, or a server error | `Failed` |
| anything else | Unrecognised | `Failed` |

**`REVIEW` is not payment.** The money is not yours yet, and it may never be. Do not release anything on a `Pending`.

---

## 4. Going live

Each gateway goes live on its own. Switching one does not affect the other, so you can run USD live while LKR is still in sandbox.

1. Get the **production** Profile ID, Access Key and Secret Key from People's Bank — a different profile, not a promotion of the test one.
2. Paste them into `live_profile_id`, `live_access_key`, `live_secret_key` in **that currency's** Settings.
3. Configure that profile's customer response pages in the Business Center (or keep passing the `override_*` URLs per request).
4. Untick **Use Sandbox**, make sure **Enabled** is ticked, and save.
5. Run one real, small payment end to end, and confirm it in the Business Center.

The switch moves everything together — endpoint, profile, access key and signing key. It never falls back across modes: an empty `live_secret_key` fails with an error naming that field, rather than quietly signing a live checkout with your test key.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Peoples Bank USD Settings doctype does not exist` (or LKR) | App not migrated | `bench --site <site> migrate` |
| `Peoples Bank (LKR) is switched off` | **Enabled** is unticked in that gateway's Settings | Fill in its credentials, tick **Enabled** |
| `Peoples Bank (USD) cannot be charged in LKR - it charges in USD only` | A caller sent a fee to the other currency's gateway | Ask `list_gateways(currency=<fee currency>)` which gateway to offer |
| LKR fees are still offered People's Bank (USD) | `Peoples Bank LKR Settings` is not **Enabled** | Configure and enable it. Until then the USD fallback is intended |
| `... is not configured for Live mode: set 'live_secret_key'` | Live credential empty | Fill it in — it will not use the sandbox one |
| `Invalid return_url: ... requires an https:// URL` | Site served over http | Serve over HTTPS, or set the site's `host_name` to its https URL |
| `Peoples Bank LKR Settings holds an invalid 'sandbox_checkout_url'` (or USD) | Override set to a non-https URL | Fix it, or clear the field to use CyberSource's endpoint |
| `decision=ERROR`, `reason_code=102`, `invalid_fields=currency` | That currency's credentials are in the other currency's Settings | Put each profile's credentials in its own currency's Settings |
| `decision=DECLINE`, `reason_code=102`, `message=Field length cannot be greater than 25.:DINVALIDDATA`, **no `invalid_fields`** | A value over 25 characters reached the processor. `auth_trans_ref_no` is the usual one — this app now truncates it. If it recurs, `reference_number` (your order id) is the one over the limit | Shorten the order id / naming series to 25 characters or fewer |
| CyberSource shows "Invalid request — field(s): signature" | Wrong secret key for the profile, or the two are from different environments | Check the profile/access/secret triple all came from the same profile |
| CyberSource rejects the request naming a signed field | A name in `signed_field_names` with no matching field. This app builds both together, so it usually means a `bill_to_*` value was rejected on its own merits — most often a malformed `bill_to_email` or a country that is not a 2-letter ISO code | Pass a valid email and a code like `LK` |
| `People's Bank response signature verification failed` | Payload tampered with, or the wrong mode is active for the profile that sent it | Check `use_sandbox` matches where the payment was made |
| `People's Bank response is for a different merchant profile` | A response correctly signed by *someone*, but not for your profile | Do not treat it as payment. Check which profile the transaction is under in the Business Center |
| `Malformed ... decision is not in the signed fields` | The POST carried `decision` outside its own signed set | Discard it — CyberSource signs every field it sends, so this was added by whoever posted to you |
| Nothing arrives at `notify_url` | Not publicly reachable, or not HTTPS | Expose the site; CyberSource requires TLS 1.2+ |

---

## Security notes specific to People's Bank

**`merchant_verified` is `True` here.** The HMAC key is issued to your merchant profile alone — unlike WebXPay, where the signing key is shared across merchants and a valid signature proves nothing about *whose* account was credited. On top of that, `req_profile_id` is checked against your settings, so a response signed for another profile is rejected even if it verifies at CyberSource. Between the two, a response that passes here was produced by CyberSource for your profile, in the currently active mode.

**Only signed fields are read.** CyberSource signs every field it sends, so anything in the POST that is not named in the response's own `signed_field_names` was added by whoever posted to you. This app discards those before reading anything, and `raw` holds the verified subset only. In particular, an unsigned `decision` or `req_reference_number` is rejected outright rather than trusted — that is the shape a forgery takes.

**The amount is checkable here.** `auth_amount` (what was authorised) and `req_amount` (what was requested) both come back inside the signed set, and `verify_response()` returns the former in preference to the latter, with `req_currency` as the currency. **Compare them against your own record** before settling — a valid signature proves what CyberSource recorded, not that it matches what you meant to charge.

**There is no nonce, so responses can be replayed.** Make your handler idempotent and ignore responses for orders that are already settled. `req_transaction_uuid` is unique per attempt and comes back in the signed set, so it is a usable de-duplication key if you store it.

**Sign everything that decides the price.** This app puts `amount`, `currency`, `reference_number`, `transaction_type`, `profile_id`, `access_key`, `transaction_uuid`, `signed_date_time` and the `override_*` URLs in the signed set, and leaves only the billing details and `auth_trans_ref_no` unsigned — matching the bank's own sample. Nothing that changes what is charged, or where the response goes, is left for the payer to alter.

---

## See also

- [Gateway configuration guides](README.md) — how sandbox/live is handled across all gateways.
- [Security model](../README.md#security-model) — what this app proves, and what your handler must still check.
