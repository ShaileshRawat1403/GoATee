# Change: 005-control-status

## Intent

Make GoATee's governing project and change control state visible and queryable through a single zero-side-effect inspection command (`goatee status` and `goatee status <change-id>`), separating contract state from machine proof state and exposing candidate build budget alignment before verification.

## Agreed Scope

### In Scope
- Command: `goatee status` providing deterministic project-level overview of all changes in `.goatee/changes/`.
- Command: `goatee status <change-id>` providing detailed single-change inspection.
- Separate visibility of Contract State (`Agreement-Status`, `Budget-Status`, `Baseline-Commit`) and Machine State (`NOT_VERIFIED`, `PASS`, `NEEDS_REVIEW`).
- Pre-verification inspection: for approved/baselined changes without a receipt, inspect `Candidate-Commit` (current HEAD) and compute read-only `baseline..HEAD` Build Budget snapshot (Expected Changes, Surface Deviations, Protected Changes).
- Verified change inspection: read authoritative snapshot from `receipt.md` (`Verified-Commit`, `Verified-At`, `Verdict`, requirement coverage, stored surface analysis).
- Read-only working tree cleanliness indicator: `Working Tree: CLEAN | DIRTY` (flagging that pre-verification budget snapshot represents committed candidate only).
- Fail-closed error handling without tracebacks for missing project, unknown change ID, malformed `change.md`, malformed `receipt.md`, and incomplete change directories.
- Zero-side-effect guarantee: status operations never mutate filesystem, repository files, `.goatee/**`, or Git state.

### Out of Scope
- No `goatee attest` command, no `review.md`, and no human attestation mechanics (deferred to 006).
- No LLM integration or agent hooks.
- No background workers, watchers, or daemon processes.
- No network access, remote API calls, or GitHub sync.
- No JSON output flag, table formatting libraries, or terminal color dependencies.
- No ancestor directory search (must run from project root where `.goatee/project.md` exists).
- No modification to existing verification mechanics or receipt schemas.

## Acceptance Criteria

- AC-01: Project overview displays all changes deterministically with separate contract state and machine verdict.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_project_status_overview
    expected: exit 0

- AC-02: Approved/baselined change without receipt displays Candidate-Commit, NOT_VERIFIED, and read-only baseline..HEAD budget snapshot.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_unverified_change_status_with_candidate_budget
    expected: exit 0

- AC-03: Verified change reads receipt snapshot and displays Verified-Commit, Verdict, coverage, and stored surface analysis.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_verified_change_status_from_receipt
    expected: exit 0

- AC-04: Detailed unverified change displays contract/build-budget metadata and criteria as PENDING_VERIFICATION.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_detailed_unverified_criteria_pending
    expected: exit 0

- AC-05: Outside-project and missing-change errors are clean, non-zero, and traceback-free.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_status_cli_error_handling
    expected: exit 0

- AC-06: Malformed change.md, malformed receipt.md, incomplete change directories, contract-receipt identity/baseline/criterion mismatches, and malformed project metadata fail closed with concise diagnostics.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_malformed_artifacts_fail_closed
    expected: exit 0

- AC-07: status and status <id> have zero filesystem and Git side effects across both clean and deliberately dirty repositories, accurately reporting working tree cleanliness.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_status_zero_side_effects
    expected: exit 0

- AC-08: All pre-existing 39 regression cases continue to pass, and the complete expanded suite passes.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-09: Standard-library only implementation and pyproject.toml dependencies = [].
  Evidence:
    type: manual
    instruction: Inspect pyproject.toml and imports across goatee/ to confirm zero external runtime dependencies.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/status.py
- tests/test_cli.py
- tests/test_status.py

### Reuse Strategy
- Reuse parse_change_contract from goatee/core/parser.py.
- Reuse get_git_changed_files and compare_budget from goatee/core/budget.py for read-only candidate comparisons.
- Reuse BuildBudget, ChangeContract, and AcceptanceCriterion from goatee/core/models.py.
- Use Python standard library only (subprocess, pathlib, re, typing, dataclasses).

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/parser.py
- goatee/core/budget.py
- goatee/core/verifier.py
- goatee/core/models.py
- goatee/templates/project.md
- goatee/templates/change.md
- goatee/templates/handoff.md
- goatee/templates/receipt.md
- pyproject.toml
- Existing verify, handoff, approve commands

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: e78c0a685a8d6f2e47a75ab071169abf71b8f682
