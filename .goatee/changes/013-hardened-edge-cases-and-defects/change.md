# Change: 013-hardened-edge-cases-and-defects

## Intent

Harden GoATee against all identified hidden defects and edge cases: extract agreed in/out scope in contract parsing, make table parsing immune to escaped pipe characters, escape pipes in rendered receipts, support Git-quoted paths with spaces/unicode, exclude mutable lifecycle bookkeeping from canonical agreement hashes, and ensure pre-commit guardrail hook works with both standalone zipapp and module execution.

## Agreed Scope

### In Scope
- Scope Extraction: Update 'goatee.core.parser.parse_change_contract' to extract 'agreed_scope_in' and 'agreed_scope_out' bullet lists.
- Table Pipe Immunity: Update 'parse_receipt' in 'goatee.core.status' and 'parse_review' in 'goatee.core.attest' to split table cells using negative lookbehind for escaped pipes ('(?<!\\\\)\\|') and unescape cell contents.
- Receipt Pipe Escaping: Update 'render_receipt_markdown' in 'goatee.core.verifier' to escape pipe characters in criterion declarations and observed strings.
- Git Quoted Path Handling: Add 'unquote_git_path' in 'goatee.core.budget' and apply it in working tree status inspection, mutation fingerprinting, and approval uncommitted checks.
- Canonical Agreement Hash Exclusions: Update 'compute_contract_hash' in 'goatee.core.parser' to exclude mutable lifecycle metadata ('Lifecycle-Status:', 'Landed-At:', 'Archived-At:'), keeping agreement hashes immutable across landing.
- Hook Compatibility: Update pre-commit hook template in 'goatee/cli.py' to run 'goatee check' if available on PATH or fallback to 'python3 -m goatee.cli check'.
- Comprehensive Unit Testing: Add regression test cases in 'tests/test_parser.py', 'tests/test_budget.py', 'tests/test_attest.py', 'tests/test_verifier.py', and 'tests/test_status.py'.

### Out of Scope
- Modifying core I-7 Operating Model steps or governance workflow.
- External dependencies.

## Acceptance Criteria

- AC-01: Contract parser extracts agreed in-scope and out-of-scope lists into ChangeContract model.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_parser"
    expected: exit 0

- AC-02: Receipt and review table parsing cleanly handles cells containing pipe characters without syntax corruption.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_attest"
    expected: exit 0

- AC-03: Budget inspection and status fingerprinting accurately handle Git paths with spaces and quotes.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_budget"
    expected: exit 0

- AC-04: Full regression test suite passes 100% green with zero errors across all modules.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-05: Verified all newly identified hidden defects are resolved with clean unit tests.
  Evidence:
    type: manual
    instruction: Inspect the implementation diffs in core and tests to confirm complete edge case hardening.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/attest.py
- goatee/core/budget.py
- goatee/core/lifecycle.py
- goatee/core/parser.py
- goatee/core/status.py
- goatee/core/verifier.py
- tests/test_attest.py
- tests/test_budget.py
- tests/test_parser.py
- tests/test_status.py
- tests/test_verifier.py

### Reuse Strategy
- Standard library: re, subprocess, hashlib, fnmatch, pathlib.
- Zero new external runtime dependencies.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- .goatee/changes/008-preflight-and-agent-bridge/**
- .goatee/changes/009-contract-integrity-and-guardrails/**
- .goatee/changes/010-lifecycle-rules-and-linting/**
- .goatee/changes/011-html-audit-and-standalone-packaging/**
- .goatee/changes/012-ci-cd-and-team-governance/**

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: 9b9216669b1246662df45d0b8ed0466182bc87b2
Contract-SHA256: 6b55f391ab1536a74939f9b744a34cc9a7fc97869394a67f11f38e2a9f3c6656
