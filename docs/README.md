# Gateway configuration guides

One guide per gateway, each covering **sandbox** (testing) and **live** (production) setup end to end — creating the Settings DocType, getting credentials, running test payments, switching over, and troubleshooting.

| Gateway | Guide | Status |
|---|---|---|
| WebXPay | **[webxpay.md](webxpay.md)** | Implemented |
| PayHere | **[payhere.md](payhere.md)** | Implemented |
| People's Bank | **[peoples_bank.md](peoples_bank.md)** | Implemented |
| Sampath Bank | — | Not implemented — [why](../sl_payment_gateways/gateways/sampath_bank.py) |
| Commercial Bank | — | Not implemented — [why](../sl_payment_gateways/gateways/commercial_bank.py) |

People's Bank's IPG turned out to be a branded **CyberSource Secure Acceptance Hosted Checkout** profile, so it is implemented against CyberSource's published protocol using the credentials the bank issues — see [peoples_bank.md](peoples_bank.md).

Sampath Bank and Commercial Bank still have no documentation to work from; their integration specs are only issued to merchants after onboarding. Each module says what would be needed to implement it. They are registered in `api.GATEWAYS` but excluded from `list_gateways()`, so they never appear as a payment option, and calling one throws rather than half-working.

## How sandbox and live are handled

Every gateway's Settings DocType holds **both credential sets at once**, with a `use_sandbox` checkbox selecting which is used:

```
use_sandbox = 1  →  sandbox_*  fields  →  the gateway's test portal
use_sandbox = 0  →  live_*     fields  →  the gateway's production portal
```

This matters because sandbox and live are genuinely separate merchant accounts at every gateway — PayHere's sandbox is a separate deployment that cannot be converted to a live account, WebXPay's staging portal issues its own RSA key pair, and People's Bank issues a CyberSource test profile distinct from the production one. Storing one set and overwriting it on each switch loses the other, and makes it easy to go live still signing with test credentials.

Three properties the code enforces, each covered by tests in [`tests/test_modes.py`](../tests/test_modes.py):

1. **Switching mode switches everything together** — checkout URL, merchant identity, and signing credentials move as one.
2. **A missing credential fails loudly.** An empty `live_merchant_secret` raises an error naming that exact field; it never falls back to the sandbox one.
3. **Cross-environment payloads are rejected.** A sandbox response replayed at a live site fails verification, and vice versa.

Errors always name both the mode and the field, e.g.:

```
PayHere Settings is not configured for Live mode: set `live_merchant_id`.
```

### Upgrading from a single credential set

Earlier versions used unprefixed `public_key` / `merchant_id` / etc. Those still work — the app falls back to them when the mode-specific field is empty — so nothing breaks on upgrade. Add the prefixed fields when you want both environments configured simultaneously.

## See also

- [Security model](../README.md#security-model) — what this app proves, and what your own handler must still check.
- [SECURITY.md](../SECURITY.md) — the full review, with residual risks per gateway.
