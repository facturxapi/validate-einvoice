# Security Policy

## Supported versions

| Release line | Supported | Supply-chain hardening (lot 1) |
|---|---|---|
| `v1` major tag (**mutable**) | yes | **no** on the current `v1` pointer (`b364f7c3`) |
| Next `v1.x` after lot 1 merge | planned | **yes** — hashed lock + external `uses` pin gate |
| `main` | development only | in progress until tagged |

Security fixes land on `main` first, then ship in the next `v1.x` tag after review.

## Reporting a vulnerability

**Verified channel:** [contact@facturxapi.com](mailto:contact@facturxapi.com)

Include: affected tag or commit SHA, minimal reproduction, expected vs observed behaviour, and impact if known.

We do **not** publish response-time SLAs in this file.

Do not open public GitHub issues for exploitable supply-chain or code-execution findings before coordinated disclosure.

### GitHub private vulnerability reporting

Private vulnerability reporting is **enabled** on `facturxapi/validate-einvoice`. Use the repository Security advisory form for coordinated disclosure. The email channel above remains valid.

## Scope

In scope for this repository:

- third-party GitHub Action pins and Python dependency integrity;
- vendored EN16931 XSLT supply chain (`vendor/`, `scripts/check_upstream.py`);
- the composite action install path (`action.yml`, `requirements.lock`);
- CI workflows under `.github/workflows/`.

Out of scope for this lot (handled separately):

- hostile XML / Saxon runtime hardening;
- consumer repository branch protection settings (documented, not modified by automation here).

## Hardening controls (lot 1)

- Every external `uses:` (except `./`) is pinned to a **full commit SHA** listed in `supply-chain/action-pins.yaml`.
- Python dependencies install from **`requirements.lock`** with `--require-hashes`.
- `scripts/check_supply_chain.py` fails on mutable external refs, unknown manifests, lock drift, or tree digest changes.
- CI workflow **Supply-chain gate** runs on every push and pull request.

## Coordinated disclosure

We credit reporters who allow reasonable remediation time. Public disclosure should wait until a fixed tag is available unless the reporter and maintainers agree otherwise.
