# People's Bank IPG — configuration and use

Covers both environments: **Sandbox** (CyberSource's test host, for testing) and **Live** (real money). The app holds credentials for both at once and switches between them with a single checkbox.

- [How People's Bank works here](#how-peoples-bank-works-here)
- [1. Find the Settings DocType](#1-find-the-settings-doctype)
- [Currencies](#currencies)
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

## 1. Find the Settings DocType

The app ships its own **Peoples Bank Settings** Single DocType (`sl_payment_gateways/sl_payment_gateways/doctype/peoples_bank_settings/`) — installing or updating the app creates it automatically. It's restricted to **System Manager** only, since it holds live payment credentials.

To open it: type `Peoples Bank Settings` into the Desk awesome-bar. Fields:

| Label | Fieldname | Type | Notes |
|---|---|---|---|
| Use Sandbox | `use_sandbox` | Check | Default `1`. Ticked = CyberSource test host, unticked = live |
| Allow LKR Charges | `allow_lkr` | Check | Default `0`. Unticked = USD only. See [Currencies](#currencies) |
| Sandbox Profile ID | `sandbox_profile_id` | Data | A UUID, e.g. `3DEEB7C4-3568-4CDF-8247-1A15527CD3B2` |
| Sandbox Access Key | `sandbox_access_key` | Data | 32 hex characters |
| Sandbox Secret Key | `sandbox_secret_key` | Password | Long hex string — the HMAC key |
| Sandbox Checkout URL | `sandbox_checkout_url` | Data | Optional, see below |
| Use CyberSource Test Billing Data | `use_test_billing_data` | Check | Default `0`. Sandbox only — see below |
| Live Profile ID | `live_profile_id` | Data | A **different** profile from the test one |
| Live Access Key | `live_access_key` | Data | |
| Live Secret Key | `live_secret_key` | Password | |
| Live Checkout URL | `live_checkout_url` | Data | Optional, see below |

**The two Checkout URL fields are almost always left blank.** Blank means "post to CyberSource's own endpoint for this mode", which is what a standard Secure Acceptance profile wants. Set one only if People's Bank has put you behind their **own** hosted front end — their QR "middle page" at `egateway.peoplesbank.lk`, which takes the same signed fields at a different URL. Whatever you set must be `https://`.

> The access key is **not** a secret: it is posted from the browser as part of the checkout form, by design. The **secret key is** — it is the HMAC key, and anyone holding it can sign a payment for your profile. It never leaves the server.

---

## Currencies

This gateway will sign a checkout in **USD** always, and in **LKR** only when
**Allow LKR Charges** is ticked. `supported_currencies()` is the single answer
to "what will it take?", and `build_checkout()` refuses anything outside it
before signing a thing.

**Leave the checkbox off until People's Bank has enabled LKR on your
CyberSource profile.** A profile without it rejects the currency outright:

```
decision=ERROR  reason_code=102  invalid_fields=currency
```

That is not a decline the payer can do anything about — they reach the hosted
page, see a card form, and cannot pay. Offering a currency that always fails is
worse than not offering it, which is why the default is off rather than
optimistic. Confirm with the bank, tick it, put one LKR payment through the
sandbox, and only then let it reach live payers.

A caller that wants to render a currency control should ask
`sl_payment_gateways.api.list_gateway_currencies()` rather than hardcoding a
list: it reports every implemented gateway's currencies, preferred first, and
tracks this checkbox without anything being redeployed.

---

## 2. Sandbox setup

**a. Get test credentials from People's Bank.** They come from your bank contact, not from a self-service signup: a test Profile ID, Access Key and Secret Key, issued against their CyberSource test account. Their integration pack (`cybs guide.docx`) is where these are listed.

**b. Fill in the Settings.** Paste into `sandbox_profile_id`, `sandbox_access_key` and `sandbox_secret_key`, leave **Use Sandbox** ticked, and save.

**c. Ask the bank for a Business Center login.** Their pack includes a *CYBS Portal User Guide*; the login is what you use to search transactions and confirm what actually happened, and it is the source of truth when a response and your records disagree.

**d. Configure the profile's response pages, or override them per request.** Secure Acceptance will not activate a profile until a customer response page is configured. Either set them once in the Business Center (**Payment Configuration → Secure Acceptance Settings → your profile → Customer Response**), or pass `return_url` / `cancel_url` / `notify_url` on every `create_payment` call and let the `override_*` fields take precedence:

```python
notify_url="/api/method/gateway_payment_return?gateway=Peoples Bank",
return_url="/api/method/gateway_payment_return?gateway=Peoples Bank",
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
from sl_payment_gateways.gateways import peoples_bank
result = peoples_bank.build_checkout("TEST-001", "10.00", "LKR", {})
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

1. Get the **production** Profile ID, Access Key and Secret Key from People's Bank — a different profile, not a promotion of the test one.
2. Paste them into `live_profile_id`, `live_access_key`, `live_secret_key`.
3. Configure that profile's customer response pages in the Business Center (or keep passing the `override_*` URLs per request).
4. Untick **Use Sandbox** and save.
5. Run one real, small payment end to end, and confirm it in the Business Center.

The switch moves everything together — endpoint, profile, access key and signing key. It never falls back across modes: an empty `live_secret_key` fails with an error naming that field, rather than quietly signing a live checkout with your test key.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Peoples Bank Settings doctype does not exist` | App not migrated | `bench --site <site> migrate` |
| `... is not configured for Live mode: set 'live_secret_key'` | Live credential empty | Fill it in — it will not use the sandbox one |
| `Invalid return_url: ... requires an https:// URL` | Site served over http | Serve over HTTPS, or set the site's `host_name` to its https URL |
| `Peoples Bank Settings holds an invalid 'sandbox_checkout_url'` | Override set to a non-https URL | Fix it, or clear the field to use CyberSource's endpoint |
| `People's Bank cannot be charged in LKR. Accepted: USD` | **Allow LKR Charges** is off | Confirm with the bank that LKR is enabled on the profile, then tick it in Settings |
| `decision=ERROR`, `reason_code=102`, `invalid_fields=currency` | LKR allowed here, but not enabled on the CyberSource profile | Untick **Allow LKR Charges** until the bank enables it |
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
