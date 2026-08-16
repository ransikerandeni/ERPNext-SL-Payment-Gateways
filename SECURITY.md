# Security review — 2026-08-14

Full review of every file in the app, plus the two Server Scripts in
[`../payment/gateway/`](../payment/gateway/) that call it. Findings are
ordered by impact. All of them are fixed unless marked **Residual**.

Severities are about *this* app in *this* deployment: a payment path
reachable by any registered participant.

---

## Critical

### C1. `create_payment` was a public endpoint that priced its own checkout

`@frappe.whitelist()` on `create_payment(gateway, order_id, amount, ...)`
made it reachable at `/api/method/sl_payment_gateways.api.create_payment`
by **any authenticated user**, with no role check and no validation that
the order existed, belonged to the caller, or cost what they said.

The business rules — ownership, draft state, not-already-paid, and the
real fee from `Participant Type` — all lived in the
`create_gateway_payment` Server Script, which the attacker simply doesn't
have to go through:

```
POST /api/method/sl_payment_gateways.api.create_payment
  gateway=WebXPay&order_id=<victim's slot>&amount=1.00&currency=LKR
```

That returns a genuinely signed checkout for 1.00 LKR. Paying it makes
WebXPay return a valid, correctly signed success response for that
order_id — and WebXPay's response carries no amount, so nothing
downstream could ever have noticed. Full fee bypass on any slot.

**Fixed.** `create_payment()` now calls `_assert_not_http_entry_point()`,
which refuses when `form_dict["cmd"]` names the function itself. It can
only be reached *through* a caller's own whitelisted method, which is
where authorisation and pricing happen. Removing the decorator outright
was not an option: Frappe routes Server Script `frappe.call()` through
`frappe.handler.execute_cmd`, which enforces the whitelist.

### C2. Same endpoint leaked the WebXPay merchant secret key

`build_checkout()` returns `secret_key` in `fields` — WebXPay's
integration requires it to be posted from the browser. Combined with C1,
any logged-in user could read the live merchant credential straight out
of an API response.

**Fixed** by C1. The value is still in the form fields, because WebXPay's
protocol demands it; it is no longer obtainable without going through an
authorised endpoint.

---

## High

### H1. `order_id` could inject a field separator into the payment string

WebXPay's payment string is built as `"%s|%s" % (order_id, amount)`, and
the gateway parses it on the pipe. An `order_id` of `SO-0001|0.01` yields
`SO-0001|0.01|1500.00` — the caller controls the amount field regardless
of what the pricing logic decided.

**Fixed.** `utils.validate_order_id()` restricts `order_id` to
`[A-Za-z0-9._-/]{1,100}` — no pipe, no whitespace, no control characters.
PayHere additionally caps it at its documented 50.

### H2. PayHere's `notify_url` pointed at an endpoint that settles nothing

`build_checkout()` hardcoded
`notify_url = /api/method/sl_payment_gateways.api.payment_return?gateway=PayHere`.
That function verifies the payload and returns it — it deliberately
touches no records. PayHere's authoritative webhook was therefore
verified and thrown away, and **no PayHere payment would ever have been
marked paid.** (`return_url` sent the browser to the Slot Allocation form,
which is presumably why this looked like it worked.)

A correctness bug rather than an exploit, but it is the difference
between taking money and recording it.

**Fixed.** `notify_url` is now a required caller-supplied value, rejected
if it isn't on this site; `create_gateway_payment.py` now passes the real
`gateway_payment_return` endpoint.

### H3. No amount verification anywhere on the return path

`verify_response()` returned only `order_id`/`status`/`raw`, and
`gateway_payment_return` marked the slot paid on `status` alone. PayHere
signs `payhere_amount`/`payhere_currency`, so the data to check was
present and unused.

**Fixed.** Both gateways now return `amount` and `currency` as top-level
keys (`None` for WebXPay, which has none to give), and
`gateway_payment_return.py` re-derives the fee and downgrades a mismatch
to `Failed` with a logged error.

---

## Medium

### M1. Unescaped user input in `frappe.throw`

`_get_gateway()` interpolated the caller-supplied gateway name into a
`frappe.throw` message. Frappe renders those as HTML in the Desk dialog,
making it a reflected XSS vector for anyone who can get a staff user to
open a crafted link. **Fixed** with `frappe.utils.escape_html`.

### M2. Non-constant-time comparison of signatures and MACs

`expected != md5sig` (PayHere) and `signature_plaintext != payment_plaintext`
(WebXPay) short-circuit on the first differing character. **Fixed** —
both go through `utils.constant_time_equals()`.

### M3. Unhandled `TypeError` on non-ASCII input (found by the fuzzer)

Introduced while fixing M2, caught before shipping:
`hmac.compare_digest` raises `TypeError` on a `str` containing non-ASCII.
A PayHere notification with an emoji in `merchant_id` or `md5sig` would
have been a 500 with a traceback on a guest-reachable endpoint, rather
than a clean rejection. **Fixed** — `constant_time_equals()` compares
UTF-8 encodings. Regression test:
`test_robustness.py::test_non_ascii_values_are_rejected_not_crashed`.

### M4. Money handled as `float`

`"%.2f" % float(amount)` accepted `nan`, `inf`, negative and zero
amounts, and rounded half-to-even — so a value could be signed at one
figure and invoiced at another. **Fixed** — `utils.format_amount()` uses
`Decimal` with `ROUND_HALF_UP`, rejects non-finite and non-positive
values, and caps at 100,000,000.

### M5. Replayable responses could un-settle a paid order

No nonce exists in either protocol, and `gateway_payment_return` wrote
`payment_status` unconditionally — so replaying an old *failed* response
would flip a paid slot back to `Failed`. **Fixed** in the Server Script:
settled orders are left alone. The replay window itself is Residual (R3).

### M6. Malformed responses parsed as declined payments

WebXPay's `dict(zip(fields, parts))` silently tolerated a short payload,
producing a dict with missing keys and a `Failed` status — a malformed or
truncated response was indistinguishable from a genuine decline.
`base64.b64decode()` without `validate=True` likewise accepted junk by
silently dropping non-alphabet characters. **Fixed** — exact field count
enforced, strict base64, empty strings rejected, and clean errors for
non-UTF-8 payloads.

### M7. Framework keys forwarded as customer data

`create_payment(..., **customer)` received everything in `form_dict`,
including `cmd` and `csrf_token`, and passed it to the gateway module.
**Fixed** — filtered against `FRAMEWORK_KEYS`.

---

## Low

### L1. PKCS#1 v1.5 padding accepted fewer than 8 padding bytes

`_rsa_public_decrypt_pkcs1_type1()` didn't enforce the scheme's minimum
padding length. Not exploitable here (the recovered plaintext is compared
in full against a fixed-length string, so there is no Bleichenbacher'06
slack to fill), but it is a deviation from the spec that costs one line.
**Fixed**, plus a ciphertext-length check.

### L2. Credential store touched on a guest endpoint

`_settings()` validated the merchant secret even for `verify_response()`,
which never needs it. **Fixed** — secret reads moved to `_secret_key()` /
`_merchant_secret()`, called only from `build_checkout()`. Pinned by
`test_verification_does_not_read_the_secret_key`.

### L3. Unhelpful errors when a Settings doctype is missing

`frappe.get_doc("WebXPay Settings")` raised a raw `DoesNotExistError`
naming a doctype the operator was never told to create. **Fixed** — both
gateways now say so explicitly, as does an unreadable RSA key.

### L4. Business logic hardcoded into a "generic" gateway module

`payhere.py` hardcoded `/app/slot-allocation/...` URLs and the string
`"Slot Allocation registration fee"`, contradicting the app's stated
design and making it unusable for any other doctype. **Fixed** — URLs and
`items` are caller-supplied; `items` defaults to `"Order <id>"`.

### L5. Customer fields passed through unfiltered

`first_name` and friends went into POST bodies (and, for PayHere,
alongside URLs) with no control-character stripping. **Fixed** —
`utils.clean_text()` strips non-printables and truncates.

---

## Residual risks — not fixable from inside this app

### R1. WebXPay responses aren't bound to your merchant account

WebXPay signs with its own key, not a per-merchant one, and the response
contains no merchant identifier and no amount. Any WebXPay merchant can
therefore produce a validly signed success response for an arbitrary
`order_id` and POST it to your return URL. The only defence is to accept
a response solely for an order you actually put into a pending state for
WebXPay, and to reconcile the figures against the WebXPay dashboard.
Documented at the top of `webxpay.py`.

PayHere is not affected: its `md5sig` is keyed on your merchant secret
and covers the merchant id, which is checked against your settings.

### ~~R2. WebXPay's response format is unconfirmed~~ — resolved

Checked against WebXPay's Redirect Integration guide
(developers.webxpay.com/Guides/Redirect-Integration), which documents the
decoded response as
`order_id|order_reference_number|date_time_transaction|status_code|comment|payment_gateway_used`
and status codes `0`/`00` = approved, `15` = declined. That is what
`webxpay.py` implements. The strict field-count check from M6 turns any
future format change into a loud error rather than a silent `Failed`.

### R3. Replay is possible within the "unsettled" window

Neither gateway sends a nonce or timestamp that this app can pin a
response to. Idempotency in the caller's handler (M5) means a replay
can't change a settled order, but a response replayed before the original
arrives is indistinguishable from it. Accept this or add your own
one-time token keyed on `order_id`.

### R4. `payment_return` is unauthenticated and does RSA work

It has to be — gateways call it with no session. It changes no state and
verifies before returning anything, but it is a public endpoint doing a
DB read and a modular exponentiation per request. Consider a
`@frappe.rate_limit` in front of your own handler if that matters; it
isn't applied here because a limit low enough to help could drop a
genuine webhook retry.

### R5. PayHere's MD5

Fixed by PayHere's API. Nothing to do from this side.

---

## Follow-up hardening — separate sandbox and live credentials

Not a vulnerability in the original code, but a foreseeable way to create
one. Both Settings doctypes held a *single* credential set with a
`use_sandbox` checkbox that changed only the checkout URL. Since sandbox
and live are entirely separate merchant accounts at both gateways
(PayHere's sandbox is a separate deployment that cannot be converted to a
live account; WebXPay's staging portal issues its own RSA key pair),
switching environments meant overwriting the other set — so the obvious
failure mode was going live while still signing with test credentials, or
pointing a live return URL at a staging account.

Each doctype now holds both sets (`sandbox_*` / `live_*`) with
`use_sandbox` selecting one, and the code enforces that:

- mode switches checkout URL, merchant identity and signing credentials
  together, never partially;
- a missing credential throws naming that exact field and mode, and never
  falls back to the other environment's value;
- cross-environment payloads are rejected — a sandbox response fails
  verification in live mode and vice versa (WebXPay on the key pair,
  PayHere on the merchant id).

Pre-split configurations still work: the unprefixed field is read when
the mode-specific one is empty. Covered by `tests/test_modes.py`.
Operator-facing guides: [docs/webxpay.md](docs/webxpay.md),
[docs/payhere.md](docs/payhere.md).

## QA pass — end-to-end flow, Server Scripts and UI

A separate pass over the whole payment journey, not just this app. Four
real defects, all fixed.

### Q1. A paid order could go unrecorded when the participant switched gateway

`create_gateway_payment` overwrites `payment_gateway` on every click, and
`gateway_payment_return` rejected any response whose gateway didn't match
it. So: open WebXPay, change your mind, click PayHere, then go back and
complete the WebXPay window — the participant is charged, the return is
rejected with an error page, and nothing records that money arrived.

**Fixed** on three levels:

- `verify_response()` now reports `merchant_verified` — whether the
  gateway's signature proves the payment reached *our* merchant account.
  True for PayHere (md5sig keyed on our secret, merchant id checked),
  False for WebXPay (shared signing key, no merchant id). This is a
  property of the protocol, so it belongs in the gateway module rather
  than being re-derived by every caller.
- `gateway_payment_return` settles a mismatched-but-merchant-verified
  response normally, and for an unverifiable one logs "Gateway payment
  needs manual reconciliation" instead of throwing — the customer no
  longer gets an error after being charged, and an admin gets a record
  to reconcile from.
- The client script warns before starting a second gateway while one is
  pending, so the situation mostly stops arising.

### Q2. The error handler was itself broken

`gateway_payment_return`'s `except` block called `frappe.get_traceback()`,
which **is not exposed in Frappe's Server Script sandbox**
(`frappe/utils/safe_exec.py`). Every verification failure therefore raised
a second time inside the handler, so the `Error Log` entry was never
written and the friendly "Could not verify payment response" never
appeared — exactly when someone would be trying to debug a failed payment.

**Fixed** — logs the exception message and the payload's field names
instead. A scan of all seven Server Scripts for other sandbox-unavailable
`frappe.*` calls found none.

### Q3. Silent failures in the payment UI

Clicking "Pay Now" and getting an empty response from the server closed
the popup and said *nothing at all* — the form sat unchanged and the
participant had no idea whether they had paid. Same for PayPal returning
no `payment_url`. The popup-blocked branch was also unreachable in one
path. **Fixed** — every failure path now names what went wrong and what
to do next, and the popup is checked before any server call.

### Q4. Payment popup looked broken while it loaded

The window opened blank (correctly — it must open synchronously or popup
blockers kill it) and stayed blank white next to a frozen form until the
gateway loaded. **Fixed** — a "Redirecting to <gateway>…" placeholder is
written immediately.

### Also fixed in the same pass

- `refresh()` issued **two** separate Payment Request queries per form
  load; merged into one. It also chained the two lookups, so "Pay Now"
  only appeared after two sequential round trips — now parallel, and the
  constant gateway list is cached instead of re-fetched on every refresh.
- A settled gateway order now skips the Payment Request query entirely
  and paints its status banner synchronously.
- Only the *latest* Payment Request was checked for "already paid"; an
  older Paid one behind a newer Failed one would have re-offered payment.
- `null` field values reached the gateway form as the string
  `"undefined"`.
- Status text is HTML-escaped before going into the banner.
- `paid_via_webxpay` renamed to `paid_via_gateway` — stale from before
  the app was multi-gateway, and it gates submission for all of them.

### QA coverage added

`slot_allocation/client_script.test.js` — 35 headless checks driving the
Client Script against a stubbed Frappe, covering the paths that are
awkward to reproduce by hand (popup blocked, empty server response,
gateway switch, an older Paid request, XSS in the status banner). Runs
with `node slot_allocation/client_script.test.js`, no dependencies.

## Also verified as sound

- The PKCS#1 v1.5 type-2 encryption padding matches
  `openssl_public_encrypt()`'s default, is randomised per call, and never
  emits a zero in the padding string. Round-trip tested against a real
  private key.
- PayHere's checkout hash and `md5sig` match the documented formula,
  tested against an independently written reference implementation.
- Signature verification is bound to *every* signed field — a valid
  notification for one order cannot be replayed as another.
- The three unimplemented bank gateways throw rather than half-work, and
  are correctly excluded from `list_gateways()`.
- No SQL is constructed anywhere in the app; no `eval`/`exec`; no
  filesystem access; no outbound network calls.

## Reproducing

```bash
pip install -e ".[dev]" && pytest && ruff check .
```
