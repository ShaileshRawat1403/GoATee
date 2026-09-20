# Change: 003-verification-receipt

## Intent

Implement deterministic, evidence-based verification and delivery receipt generation
for GoATee changes via `goatee verify <change-id>`, enabling FDCs and non-developers
to independently prove that an implementation satisfied its agreement without relying
on agent claims.

## Agreed Scope

### In Scope
- Command `goatee verify <change-id>`.
- Pre-verification checks: contract approved, baseline exists, candidate HEAD committed (`git status --porcelain` clean).
- Gated execution: budget comparison runs before executing any commands; unapproved surface or protected-path deviations block command execution and yield `NEEDS_REVIEW`.
- Execution and capture of declared automated acceptance criteria supporting narrow `exit N` grammar only.
- Halt on mutation: take a Git-visible state fingerprint (porcelain status + content identity) before and after each command; if a command mutates Git-visible state, verification immediately halts, remaining criteria are marked `NOT_RUN`, and verdict is `NEEDS_REVIEW`.
- Explicit representation of manual acceptance criteria without fabricated completion (`Pending human review` -> `NEEDS_REVIEW`).
- Compilation of `.goatee/changes/<change-id>/receipt.md` with commit provenance, diff stats, coverage matrix, budget deviations, mutation report, and delivery verdict.
- Bounded diagnostic excerpt in receipt (no raw unbounded stdout/stderr dumps).
- Explicit delivery verdict: `PASS` only if all automated criteria pass, zero manual criteria are pending, zero deviations exist, and zero verification mutations occurred; `NEEDS_REVIEW` otherwise.
- Collapse execution machinery into `goatee/core/verifier.py` (no premature `engine/` package).

### Out of Scope
- Interactive prompt for manual evidence during CLI run (manual ACs legitimately produce `NEEDS_REVIEW` in Phase 3).
- Automatic deviation approval or automatic remediation/fixing of failing tests.
- Shell pipeline mini-languages (pipes, `&&`, redirection); execute commands as discrete subprocesses.
- Semantic code or AST inspection.
- Package-manager-specific dependency graph analysis.
- Third-party runtime dependencies (pure Python standard library).
- Any LLM/API calls.

## Acceptance Criteria

- AC-01: Verification blocks if working tree contains any uncommitted Git-visible changes
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_verify_blocks_dirty_worktree
    expected: exit 0

- AC-02: Verification blocks if change has no Baseline-Commit or is in DRAFT
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_verify_blocks_unapproved_or_missing_baseline
    expected: exit 0

- AC-03: Budget deviations gate evidence execution without running commands
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_budget_deviations_gate_execution
    expected: exit 0

- AC-04: Automated criteria support narrow exit N grammar and capture bounded results
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_automated_criteria_execution
    expected: exit 0

- AC-05: Manual criteria are honestly represented as pending review yielding NEEDS_REVIEW
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_manual_criteria_handling
    expected: exit 0

- AC-06: Verification halts immediately if a command mutates Git-visible repository state
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_halt_on_git_visible_mutation
    expected: exit 0

- AC-07: Verification generates complete receipt.md with bounded diagnostics and verdict
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_verify_generates_receipt_md
    expected: exit 0

- AC-08: Delivery verdict evaluates to PASS strictly when all conditions are satisfied
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_verdict_evaluation_strict_pass
    expected: exit 0

- AC-09: Phase 3 adds zero external runtime dependencies and collapses into goatee/core/verifier.py
  Evidence:
    type: manual
    instruction: Inspect repository structure and imports; confirm no engine/ package and stdlib only.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/verifier.py
- goatee/templates/receipt.md
- tests/test_verifier.py
- tests/test_cli.py
- README.md

### Reuse Strategy
- Existing Phase 1 ChangeContract & Project models
- Existing Phase 1 parser and metadata functions
- Existing Phase 2 `compare_budget` and `get_git_changed_files`
- Git CLI for status, diff --stat, and HEAD inspection
- Python stdlib `subprocess`, `shlex`, `hashlib`, `pathlib`

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/parser.py
- goatee/core/models.py
- goatee/core/budget.py
- goatee/templates/project.md
- goatee/templates/change.md
- goatee/templates/handoff.md
- Phase 1 & Phase 2 CLI commands (init, new, approve, handoff)

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: bbab8c9cbb6db99f6c352b30c76b7d14d1c2ee11
