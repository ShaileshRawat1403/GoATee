# Change: 006-human-attestation

## Intent

Introduce a deterministic, auditable human attestation mechanism (`goatee attest`) that captures attributed human review of manual acceptance criteria into a receipt-bound review artifact (`review.md`) with append-preserving review history, establishing a dual-verdict verification model (`Machine Verdict` and `Human Review`) without modifying machine receipts or bypassing automated checks.

## Agreed Scope

### In Scope
- Command: `goatee attest <change-id> --criterion <AC-ID> --verified --note "<text>"` for positive attestation of manual criteria.
- Command: `goatee attest <change-id> --criterion <AC-ID> --rejected --note "<text>"` for negative attestation of manual or automated criteria.
- Command: `goatee attest <change-id> --rejected --note "<text>"` for change-level human rejection.
- Clean Worktree Prerequisite: `goatee attest` requires a completely clean Git working tree before execution and verifies that `receipt.md` is committed and byte-identical to HEAD.
- Mutation Boundary: `goatee attest` mutates ONLY `.goatee/changes/<change-id>/review.md`. HEAD, the Git index, `change.md`, `receipt.md`, `project.md`, and application files remain untouched.
- Review Source of Truth: `## Review Event History` is the authoritative record. The coverage matrix and `Review-Verdict` are derived projections; discrepancies between stored projections and event history fail closed.
- Receipt Content Binding: `review.md` binds to `Verified-Commit`, `Receipt-Commit`, and `Receipt-SHA256` (SHA-256 content binding). Any modification to `receipt.md` invalidates existing reviews (fail closed on stale reviews without silent rebinding).
- Attestation Eligibility: Only criteria declared with `type: manual` and whose receipt machine status is `NEEDS_REVIEW` may be attested `--verified`. Attempting to attest automated criteria or manual criteria not in `NEEDS_REVIEW` fails closed.
- Rejection Semantics: Manual criterion rejections may be superseded by later verified events. Automated criterion rejections and change-level rejections are sticky for the receipt snapshot.
- Machine Proof Invariant: Human Review becomes `ACCEPTED` only if all automated criteria are `VERIFIED`, deviations/protected changes/mutations are 0, and all manual criteria are `VERIFIED`. Flawed machine receipts block acceptance without manufacturing human rejection.
- Dual-Verdict Status Reporting: `goatee status` and `goatee status <change-id>` report both `Machine Verdict` (`PASS | NEEDS_REVIEW`) and `Human Review` (`PENDING | ACCEPTED | REJECTED`). Human review is never implicit.
- Reviewer Attribution: Attributed to local Git configuration (`git config user.name` and `user.email`) explicitly as "Git-configured attribution".
- Note Normalization: `--note` is required, trimmed, single-line only (rejects CR/LF), with pipe and backslash characters safely escaped in table rendering.
- Fail-Closed Error Handling: Fails closed cleanly without tracebacks on dirty working trees, missing receipts, uncommitted receipts, stale reviews, tamper mismatches, unknown criteria, empty notes, or missing Git config.

### Out of Scope
- No cryptographic signatures (GPG/SSH). Attribution relies strictly on local Git configuration.
- No remote web services, OAuth, single-sign-on, or network identity providers.
- No LLM evaluation or autonomous agent attestation.
- No mutation of `receipt.md`, `change.md`, or `project.md`.
- No modification to core verification mechanics (`goatee verify`) or core budget comparison.
- No automatic review migration or silent rebinding to new receipts.
- No third-party runtime dependencies (`pyproject.toml` remains `dependencies = []`).

## Acceptance Criteria

- AC-01: Positive attestation of manual criterion creates/updates `review.md` with deterministic event record and derived projections.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_attest_manual_criterion_success
    expected: exit 0

- AC-02: Receipt binding strictly validates Verified-Commit, Receipt-Commit, and Receipt-SHA256, failing closed on tamper or mismatch.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_receipt_binding_and_tamper_detection
    expected: exit 0

- AC-03: Stale review detection fails closed when receipt is updated or re-verified without silent rebinding.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_stale_review_fails_closed
    expected: exit 0

- AC-04: Positive attestation eligibility strictly rejects `--verified` for automated criteria and manual criteria not in NEEDS_REVIEW.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_positive_attestation_eligibility
    expected: exit 0

- AC-05: Rejection semantics enforce manual supersession, automated stickiness, and change-level stickiness.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_rejection_supersession_and_stickiness
    expected: exit 0

- AC-06: Event history source-of-truth validation fails closed if stored matrix or verdict differs from event projection, enforcing sequence ordering and byte-identical append preservation.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_event_history_sequence_preservation_and_projections
    expected: exit 0

- AC-07: Clean worktree requirement before attest and proof that `goatee attest` mutates ONLY `review.md`.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_clean_worktree_prerequisite_and_zero_side_effects
    expected: exit 0

- AC-08: Dual-verdict status reporting correctly handles no-review, valid committed review, uncommitted review, and stale review with separate machine and human criterion states.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_dual_verdict_status_states
    expected: exit 0

- AC-09: CLI argument validation, note normalization/escaping, and fail-closed error handling without tracebacks.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_attest_cli_validation_and_errors
    expected: exit 0

- AC-10: Complete test suite passes with zero regressions across pre-existing 47 tests.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-11: Standard-library only implementation and pyproject.toml dependencies = [].
  Evidence:
    type: manual
    instruction: Inspect pyproject.toml and imports across goatee/ to confirm zero external runtime dependencies.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/attest.py
- goatee/core/status.py
- tests/test_cli.py
- tests/test_attest.py
- tests/test_status.py

### Reuse Strategy
- Reuse `parse_change_contract` from `goatee/core/parser.py`.
- Reuse `ReceiptSnapshot` and receipt parser from `goatee/core/status.py` (extended for mutations).
- Reuse Git helpers and inspection logic from `goatee/core/budget.py`.
- Python standard library only (`hashlib`, `subprocess`, `pathlib`, `re`, `typing`, `dataclasses`, `datetime`).

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/parser.py
- goatee/core/budget.py
- goatee/core/verifier.py
- goatee/core/models.py
- goatee/templates/**
- README.md
- THIRD_PARTY.md
- pyproject.toml

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: ca6929d108b1d1fdbb910f1a342aa61f21ce6ed8
