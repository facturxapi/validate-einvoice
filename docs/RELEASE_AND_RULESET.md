# Release and branch protection policy

This document describes the **expected** GitHub settings for `facturxapi/validate-einvoice`. The repository automation does **not** modify organization or repository settings; maintainers apply these rules manually or via org policy.

## Release model

| Artifact | Policy |
|---|---|
| Marketplace major tag `v1` | **Mutable** convenience channel. The current pointer (`b364f7c3`) predates lot-1 supply-chain hardening. |
| Patch tags `v1.x.y` | Immutable once published; never force-move. Lot 1 should ship as a new `v1.x.y` with the hardened tree SHA in release notes. |
| `main` | Integration branch; must pass all CI gates before tag cut. |

### Two-phase consumer pinning

1. **Pre–lot-1 (`@v1` → `b364f7c3`)** — validation behaviour only; no hashed lock / external `uses` pin gate.
2. **Post–lot-1 release** — publish the merge commit SHA in GitHub Releases; consumers pin that 40-character SHA for a frozen hardened tree.

Do not document `b364f7c3` as the hardened recommendation after lot 1 merges.

### Tag cut checklist

1. `scripts/selftest.py` green locally or via CI matrix.
2. `scripts/check_supply_chain.py` green (action pins, hashed lock, tree digest).
3. `scripts/check_upstream.py` unchanged or intentionally updated with vendor pin review.
4. Update `README.md` / `NOTICE` only when the user-facing contract changes.
5. Create annotated tag `v1.x.y` on the merge commit; publish release notes referencing the commit SHA.

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

| Setting | Status (Aug 2026) | Action |
|---|---|---|
| Private vulnerability reporting | **disabled** (`enabled=false`) | Enable in repo Settings → Code security before advertising in `SECURITY.md` |
| Contact email | `contact@facturxapi.com` | Published in `SECURITY.md` without SLA claims |

## What this repo does not automate

- Enabling GitHub Advanced Security or org-wide rulesets.
- Marketplace listing promotion.
- Signing tags or artifacts (future hardening may add this).

Maintainers record external settings changes in the release notes when they affect consumer trust boundaries.
