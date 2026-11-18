# Change: 014-trust-and-correctness

## Intent

Harden GoATee's verification verdicts, CI governance, active change resolution, and lifecycle landing to ensure machine evidence can never be bypassed or overridden by human review, ambiguous selection, or forged artifacts, while preserving the legitimate dual-verdict delivery model.

## Agreed Scope

### In Scope
- Differentiate `FAIL` from `NEEDS_REVIEW` in `verify_change()`: any failed automated criterion, timeout, budget deviation, blast radius violation, or Git mutation yields `verdict = "FAIL"`.
- Enforce that `--allow-needs-review` in CLI `verify` and CI `run_ci` only permits pending human acceptance when all machine evidence is verified green; it never exits 0 on failed machine evidence.
- Prevent human review (`Review-Verdict: ACCEPTED`) from overriding failed machine evidence in CI and landing.
- Preserve the dual-verdict landing model: `land_change()` permits landing when either the receipt is `PASS`, or the receipt had machine-green `NEEDS_REVIEW` and valid, untampered, non-stale human acceptance (`ACCEPTED`) resolves all manual criteria.
- Replace ambiguous change selection in `resolve_active_change_id()` with deterministic fail-closed behavior (no picking `unverified[-1]` when multiple candidates exist). Treat `GITHUB_HEAD_REF` solely as a discovery hint that must be validated against actual change contracts, failing closed on ambiguity.
- Preserve explicit human approval authority boundaries and document that contract hashes provide integrity checking (anti-tamper), not independent proof of authorization.
- Preserve all historical change records, including `002-handoff-budget`, without allowing them to be accidentally selected on clean checkouts.
- Add comprehensive negative regression tests covering automated failures, timeouts, budget violations, Git mutations, forged or stale review evidence, post-verification implementation changes, and legitimate manual acceptance.

### Out of Scope
- Usability features and drafting shortcuts (deferred to Sprint 015).
- Combining draft creation with contract approval (approval remains an explicit authority boundary).
- Staged-only Git hook changes (deferred to Sprint 015).
- Modifying or deleting historical change artifacts.

## Acceptance Criteria

- AC-01: Verification returns `FAIL` verdict when automated checks fail, time out, or budget/mutations occur
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verdict_hardening.py -k "test_failed_automated_check_yields_fail_verdict"
    expected: exit 0

- AC-02: CI `--allow-needs-review` exits 1 when any machine evidence fails
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verdict_hardening.py -k "test_ci_allow_needs_review_fails_on_machine_failure"
    expected: exit 0

- AC-03: CI rejects human review override when machine evidence fails
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verdict_hardening.py -k "test_ci_human_accepted_does_not_override_failed_machine_evidence"
    expected: exit 0

- AC-04: Ambiguous active change resolution fails closed without guessing historical changes
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verdict_hardening.py -k "test_ambiguous_active_change_resolution_fails_closed"
    expected: exit 0

- AC-05: Lifecycle landing verifies machine evidence success, receipt hash, review integrity, and legitimate manual acceptance
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verdict_hardening.py -k "test_land_change_validates_receipt_and_integrity"
    expected: exit 0

- AC-06: Comprehensive regression suite for timeouts, mutations, stale evidence, and post-verification modifications passes
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verdict_hardening.py
    expected: exit 0

- AC-07: Full GoATee test suite passes with zero regressions
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-08: Manual inspection of verification verdicts and security boundaries
  Evidence:
    type: manual
    instruction: Inspect verifier.py, ci.py, lifecycle.py, and status.py to confirm fail-closed security and separation of human and machine authority.

## Build Budget

### Expected Surface
- goatee/core/verifier.py
- goatee/core/ci.py
- goatee/core/lifecycle.py
- goatee/core/status.py
- goatee/cli.py
- tests/test_verdict_hardening.py
- tests/test_ci.py
- tests/test_verifier.py
- tests/test_lifecycle.py
- tests/test_status.py

- tests/test_cli.py
### Reuse Strategy
- Reuse `parse_metadata_block` and `compute_contract_hash` from `goatee.core.parser`.
- Reuse `verify_receipt_prerequisites` and receipt hash validation from `goatee.core.attest`.
- Reuse `VerificationResult` and `CriterionResult` models from `goatee.core.verifier`.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- .github/workflows/
- goatee/templates/
- goatee/core/rules.py
- goatee/core/linter.py
- dist/

## Amendments
- [2026-11-18 06:49:36Z] Added surface 'tests/test_cli.py' (Attributed to Shailesh Rawat <shailesh.rawat1403@gmail.com>; Reason: Updated test_git_inspection_fails_closed to assert FAIL on simulated git corruption)

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: f37054c2d85804c62a166732f7bc62c5a93b99b5
Contract-SHA256: b0d12acb9a006c3f6dc62bafde08d72b8413caa008d7bbb26577cf4d1e6c1a0c
