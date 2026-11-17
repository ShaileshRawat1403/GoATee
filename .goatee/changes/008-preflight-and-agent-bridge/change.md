# Change: 008-preflight-and-agent-bridge

## Intent

Advance GoATee from a hardened post-build verification engine into a seamless daily driver for developers, consultants, and AI coding agents by adding working-tree pre-flight budget inspection (`goatee check`), interactive human attestation (`goatee attest -i`), a zero-dependency stdio Model Context Protocol (MCP) server (`goatee mcp`), and active change context auto-detection.

## Agreed Scope

### In Scope
- Implement `goatee check [change-id]` to compare uncommitted (staged + unstaged) working tree modifications against an approved Build Budget without side effects.
- Implement `goatee attest [change-id] -i / --interactive` wizard to guide human reviewers through pending manual criteria sequentially with single-line notes and Git-configured attribution.
- Implement `goatee mcp` as a zero-dependency, pure standard-library stdio JSON-RPC 2.0 MCP server exposing tools (`goatee_get_contract`, `goatee_check_budget`, `goatee_get_status`).
- Implement active change resolution when `change_id` is omitted in CLI commands, inferring from Git branch name (`change/<id>` or `<id>`) or single active unverified change in `.goatee/changes/`.
- Maintain strict zero runtime external dependencies (Python stdlib only).
- Preserve all existing proof boundary invariants (fail-closed, clean working tree for `verify` and `attest`, non-interactive `verify`, receipt immutability).
- Update README.md with comprehensive documentation for `check`, `attest -i`, `mcp`, and change context resolution.

### Out of Scope
- No third-party package dependencies (no external `mcp` SDK, no click, no rich).
- No changes to existing receipt schemas or verification exit-code contracts.
- No automatic approval of budget deviations.
- No retroactive modification of historical changes (002 through 007).
- No package version bump or release tag (remains pre-release v0.2.x -> v0.3.0 preparation).

## Acceptance Criteria

- AC-01: Working-tree pre-flight budget inspection ('goatee check') accurately evaluates staged and unstaged changes against approved expected surface and blast radius with zero side effects.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_check"
    expected: exit 0

- AC-02: Interactive human attestation ('goatee attest -i') guides reviewers sequentially through pending criteria, enforces mandatory notes and clean worktree, and appends valid review events to review.md.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_attest_interactive"
    expected: exit 0

- AC-03: Pure standard library stdio MCP server ('goatee mcp') handles initialize, tools/list, and tools/call (goatee_get_contract, goatee_check_budget, goatee_get_status) per JSON-RPC 2.0.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_mcp"
    expected: exit 0

- AC-04: Active change-id is correctly inferred from Git branch name or single unverified change when omitted from CLI commands.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_active_change"
    expected: exit 0

- AC-05: Complete regression test suite passes with zero errors and zero failures.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-06: Public README and command help accurately document the new pre-flight, interactive attestation, MCP server, and context resolution workflows.
  Evidence:
    type: manual
    instruction: Review README.md and goatee --help to ensure all new commands, options, and workflows are clearly and factually documented without overstating trust guarantees.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/budget.py
- goatee/core/attest.py
- goatee/core/status.py
- goatee/core/mcp.py
- tests/test_check.py
- tests/test_attest_interactive.py
- tests/test_mcp.py
- tests/test_cli.py
- README.md

### Reuse Strategy
- Reuse `compare_budget` in `goatee/core/budget.py` for budget evaluation.
- Reuse `record_attestation` and `derive_review_state` in `goatee/core/attest.py` for review recording.
- Reuse `get_change_report` and `get_project_status` in `goatee/core/status.py` for status reporting and MCP data providers.
- Reuse standard library `json`, `sys`, and `argparse`.
- No new external runtime dependencies.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/models.py
- goatee/core/parser.py
- goatee/core/verifier.py
- goatee/templates/project.md
- goatee/templates/change.md
- goatee/templates/handoff.md
- goatee/templates/receipt.md
- .goatee/project.md
- .goatee/changes/002-handoff-budget/**
- .goatee/changes/003-verification-receipt/**
- .goatee/changes/004-proof-hardening/**
- .goatee/changes/005-control-status/**
- .goatee/changes/006-human-attestation/**
- .goatee/changes/007-release-prep/**

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: a2d7e377325b0eccecc9ee4bc864f67240124b41
