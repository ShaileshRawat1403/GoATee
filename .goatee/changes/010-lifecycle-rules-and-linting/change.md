# Change: 010-lifecycle-rules-and-linting

## Intent

Complete the change lifecycle with landing and archival ('goatee land', 'goatee archive'), eliminate invalid or conflicting contracts pre-approval with semantic linting ('goatee lint'), scaffold native AI agent boundaries ('goatee rules export'), and resolve wildcard budget matching for robust developer guardrails.

## Agreed Scope

### In Scope
- Implement semantic contract linter ('goatee lint') detecting Expected Surface vs Blast Radius overlaps, path traversal attempts, and malformed criteria.
- Implement change landing ('goatee land') requiring an ACCEPTED review, updating lifecycle state to LANDED, moving change directory to .goatee/archive/<change-id>, and clearing .goatee/.active.
- Implement change archival ('goatee archive') for explicit archiving of changes into .goatee/archive/<change-id>.
- Update 'goatee status' to exclude archived changes by default, adding '--all' and '--archived' flags to inspect historical archives.
- Implement native agent rules scaffolding ('goatee rules export') supporting targets 'claude', 'cursor', 'windsurf', 'gemini', and 'all' with bounded markers.
- Fix path wildcard matching in 'matches_path_entry' to support trailing '/**' and '/*' directory globs.
- Update 'cmd_new' to automatically point .goatee/.active to newly created change contracts.
- Maintain zero runtime dependencies (Python standard library only).
- Update README.md and CLI help output.

### Out of Scope
- No standalone zipapp packaging (Sprint 011).
- No HTML/PDF certificate generation (Sprint 011).
- No GitHub Actions CI workflow (Sprint 012).
- No third-party package dependencies.

## Acceptance Criteria

- AC-01: Contract linter ('goatee lint') detects overlapping surface/blast-radius paths, traversal attacks, and malformed acceptance criteria, exiting with code 1 and actionable diagnostics.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_linter"
    expected: exit 0

- AC-02: Change landing and archiving ('goatee land' and 'goatee archive') enforce review acceptance, relocate directories to .goatee/archive/, clear .active, and isolate archives from default status output.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_lifecycle"
    expected: exit 0

- AC-03: Native agent rules exporter ('goatee rules export') writes valid rule definitions for Claude, Cursor, Windsurf, and Gemini while preserving surrounding custom content.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_rules"
    expected: exit 0

- AC-04: Path budget matcher accurately handles glob directory wildcards ('/**' and '/*') across expected surface and blast radius checks.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_budget_wildcards"
    expected: exit 0

- AC-05: Full test suite passes 100% green with zero errors across all modules.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-06: All new commands (lint, land, archive, rules) and options are accurately documented in README.md and CLI help output.
  Evidence:
    type: manual
    instruction: Inspect README.md and goatee --help to verify documentation of lint, land, archive, and rules export commands.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/budget.py
- goatee/core/status.py
- goatee/core/parser.py
- goatee/core/linter.py
- goatee/core/rules.py
- goatee/core/lifecycle.py
- tests/test_linter.py
- tests/test_lifecycle.py
- tests/test_rules.py
- tests/test_budget_wildcards.py
- tests/test_cli.py
- README.md

- goatee/core/attest.py
### Reuse Strategy
- Standard library: pathlib, hashlib, shutil, re, sys, argparse, subprocess.
- Reuse parse_change_contract, parse_metadata_block in goatee/core/parser.py.
- Reuse compare_budget in goatee/core/budget.py.
- Reuse resolve_or_fail_change_id in goatee/cli.py.
- Zero external runtime dependencies.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/models.py
- goatee/core/verifier.py
- goatee/core/mcp.py
- .goatee/project.md
- .goatee/changes/002-handoff-budget/**
- .goatee/changes/003-verification-receipt/**
- .goatee/changes/004-proof-hardening/**
- .goatee/changes/005-control-status/**
- .goatee/changes/006-human-attestation/**
- .goatee/changes/007-release-prep/**
- .goatee/changes/008-preflight-and-agent-bridge/**
- .goatee/changes/009-contract-integrity-and-guardrails/**

## Amendments
- [2026-11-17 11:31:58Z] Added surface 'goatee/core/attest.py' (Attributed to Shailesh Rawat <shailesh.rawat1403@gmail.com>; Reason: Support git log --follow for archived change reviews in load_and_validate_review)

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: 05c40f1cb7c58327b869da660b8e4145f9267b7a
Contract-SHA256: c98554644aff47202d9d05cdac4659086977c27368e5c2fe86438fba5228e35b
