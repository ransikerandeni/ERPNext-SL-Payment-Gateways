# Changelog

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
