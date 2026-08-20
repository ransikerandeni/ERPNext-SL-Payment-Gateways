# Changelog

## 0.3.1 — 2026-08-20

### Fixed

- **WebXPay responses are accepted again.** Their Redirect Integration
  guide documents a six-field response; a real staging transaction sends
  eight — the documented six plus undocumented `requested_amount` and
  `transaction_amount`. `verify_response()` rejected the real shape with
  `Unexpected WebXPay response format: expected 6 fields, got 8`, so
  every genuinely approved payment failed at the return handler and the
  order stayed unpaid. Both lengths are now accepted; any other count is
  still rejected as malformed.
- **A gateway return no longer logs the browser out of ERPNext.** The
  return is a cross-site POST, so SameSite=Lax withholds the session
  cookie and it authenticates as Guest even from a signed-in browser;
  Frappe then set `sid=Guest` on the response, landing an anonymous
  session in the browser that *was* signed in. `payment_return()` now
  drops the session cookie from its own response (before verification,
  so error responses are covered too), leaving whatever the browser
  already holds untouched. Nothing on this endpoint depends on session
  state — the payload is verified cryptographically, not by who sent it.

### Added

- WebXPay `verify_response()` now returns the captured amount as
  `amount` when the eight-field response carries it (read from the signed
  blob, never from the identically named unsigned POST parameters), so
  callers can price-check a WebXPay payment instead of having to take the
  status on trust. Still `None` on a six-field response, and the currency
  is never sent at either length — see [docs/webxpay.md](docs/webxpay.md).

### Confirmed

- The response **field order follows WebXPay's guide**, not the
  contradictory comment in their `php-response.txt` sample. The live
  payload `...|00|00 - Approved|40|10.00|10.00` puts `status_code` in
  position 4 exactly as the guide's own worked example does. This
  resolves the open item in the project README §6h.

## 0.3.0 — 2026-08-17

### Fixed

- `mode_value()` / `mode_password()` in [utils.py](sl_payment_gateways/utils.py)
  now strip outer whitespace from credential/key fields before use. A
  stray leading/trailing space or newline from copy-paste (browser,
  clipboard manager, source page) previously produced an opaque
  "unreadable RSA public key" or a gateway-side rejection with no useful
  diagnostic. Internal PEM line breaks are untouched.

### Added

- WebXPay checkout now sends an `enc_method` field ("Mechanism"),
  matching every request in WebXPay's own published sample code — this
  field is undocumented in their integration guide's own parameter
  table but present on every sample form, and its absence is a
  plausible cause of `error=442&message=Invalid encryption` from
  WebXPay's server. New optional `sandbox_enc_method` / `live_enc_method`
  fields on `WebXPay Settings` let you override it per mode; left blank,
  the app sends WebXPay's own published sample value as a default. See
  [docs/webxpay.md](docs/webxpay.md).

## 0.2.0 — 2026-08-16

### Added

- `WebXPay Settings` and `PayHere Settings` are now app-owned Single
  DocTypes (`sl_payment_gateways/sl_payment_gateways/doctype/`), created
  automatically by `bench install-app` / `bench migrate`. Previously the
  app shipped no DocTypes at all and required creating both by hand via
  Setup → DocType → New on every site. Both remain restricted to the
  System Manager role. See [docs/webxpay.md](docs/webxpay.md) and
  [docs/payhere.md](docs/payhere.md).

### Changed

- `bench uninstall-app` now removes these two DocTypes (and the
  credentials stored in them) along with the app, since they're part of
  its schema rather than something you created separately. Back up
  first if you might reinstall later.

### Upgrade notes

- If you had already created `WebXPay Settings` / `PayHere Settings` by
  hand on a site running an earlier version, `bench migrate` reconciles
  the existing doctype with the app-owned definition by fieldname —
  your stored credentials are preserved.

## 0.1.0 — Initial release

- Pluggable Sri Lankan payment gateway framework for ERPNext/Frappe.
- WebXPay (RSA redirect integration) and PayHere (MD5 Checkout API)
  implemented; People's Bank, Sampath Bank, Commercial Bank scaffolded
  pending public integration specs.
- Sandbox/live credential separation per gateway.
- Security hardening pass — see [SECURITY.md](SECURITY.md).
