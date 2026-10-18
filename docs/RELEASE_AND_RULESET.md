# Release and branch protection policy

This document describes the **expected** GitHub settings for `facturxapi/validate-einvoice`. The repository automation does **not** modify organization or repository settings; maintainers apply these rules manually or via org policy.

## Release model

| Artifact | Policy |
|---|---|
| Marketplace major tag `v1` | **Mutable** convenience channel, moved to each new `v1.x.y`. It points at `v1.3.0` (EN16931 1.3.17 by default). |
| Patch tags `v1.x.y` | Immutable once published; never force-move. Release notes name the tagged commit SHA. |
| `main` | Integration branch; must pass all CI gates before tag cut. |

### Consumer pinning

- `@v1` follows the latest `v1.x.y`; `@v1.x.y` names one release; the 40-character commit SHA from the release notes is the frozen tree.
- Every tag from `v1.1.1` on carries the hashed lock, the external `uses` pin gate and the DOCTYPE/entity gate. `v1.0.0` and `v1.1.0` (`b364f7c3`) do not; never recommend them.

### Tag cut checklist

1. `scripts/selftest.py` green locally or via CI matrix.
2. `scripts/check_supply_chain.py` green (action pins, hashed lock, tree digest).
3. `scripts/check_upstream.py` unchanged or intentionally updated with vendor pin review.
4. Update `README.md` / `NOTICE` only when the user-facing contract changes.
5. Create annotated tag `v1.x.y` on the merge commit; publish release notes referencing the commit SHA.
6. Prove the tag as a consumer from a throwaway branch (`uses: facturxapi/validate-einvoice@v1.x.y`, expected `report-sha256` for official examples and mutants), then move `v1` and delete the branch. That branch is red on the Supply-chain gate and the self-test by design: both refuse an external `uses` by tag, and its workflows are outside the tree digest.

## Required CI checks (recommended ruleset)

Configure branch protection on `main` with **required status checks**:

| Check | Workflow |
|---|---|
| Self-test matrix | `Self-test EN16931 action` / `selftest` job |
| Supply-chain gate | `Supply-chain gate` / `supply-chain` job |

Optional but recommended:

- `EN16931 upstream drift` (scheduled; not required on every PR).

### Pull request rules (recommended)

- Require pull request before merge.
- Require at least one approving review for changes touching `vendor/`, `requirements.lock`, `supply-chain/`, or `.github/workflows/`.
- Dismiss stale approvals when new commits are pushed.
- Block force-push and deletion on `main`.

## Dependabot and pin updates

- Bump third-party Action SHAs only together with `supply-chain/action-pins.yaml`, workflow edits, and `supply-chain/tree.sha256`.
- Bump `saxonche` only together with `requirements.txt`, `requirements.lock`, and supply-chain tests.

## Security reporting prerequisites

| Setting | Status (Oct 2026) | Action |
|---|---|---|
| Private vulnerability reporting | **enabled** (`enabled=true`, checked 2026-10-11) | Advertised in `SECURITY.md` |
| Contact email | `contact@facturxapi.com` | Published in `SECURITY.md` without SLA claims |

## What this repo does not automate

- Enabling GitHub Advanced Security or org-wide rulesets.
- Marketplace listing promotion.
- Signing tags or artifacts (future hardening may add this).

Maintainers record external settings changes in the release notes when they affect consumer trust boundaries.
