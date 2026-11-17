# Change: 009-contract-integrity-and-guardrails

## Intent

Eliminate the self-approving agent loophole by binding approved change contracts with cryptographic tamper-detection hashes (Contract-SHA256), establishing an auditable budget amendment workflow ('goatee amend'), installing zero-dependency Git pre-commit guardrail hooks ('goatee hook install'), and enabling AI coding agents to propose technical build budgets via MCP tool-calling ('goatee_propose_budget').

## Agreed Scope

### In Scope
- Implement canonical contract content hashing (`compute_contract_hash`) excluding mutable state fields.
- Record `Contract-SHA256` in `change.md` at `goatee approve`.
- Enforce contract tamper detection in `goatee verify`: immediately fail closed if `change.md` content was altered after approval.
- Record `Contract-SHA256` in `receipt.md` and display contract integrity status in `goatee status`.
- Implement `goatee amend [change-id] --add-surface <path> --note <rationale>` to provide an auditable, human-governed budget expansion mechanism.
- Implement `goatee hook install` and `goatee hook uninstall` to install a zero-dependency Git `.git/hooks/pre-commit` hook that triggers `goatee check`.
- Expose `goatee_propose_budget` in the pure stdlib MCP server (`goatee/core/mcp.py`) allowing agents to propose Build Budgets for DRAFT contracts.
- Maintain zero external runtime dependencies (Python standard library only).
- Preserve all existing proof boundary invariants.
- Update documentation in README.md.

### Out of Scope
- No third-party package dependencies.
- No retroactive alteration of historical changes (002 through 008).
- No automatic approval of amended budgets without human intervention.
- No remote CI/CD hosting setup (remains local Git hook and CLI).

## Acceptance Criteria

- AC-01: Change contract approval computes and locks Contract-SHA256, and verification fails closed if the approved contract is tampered with post-approval.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_integrity"
    expected: exit 0

- AC-02: Human-directed budget amendment ('goatee amend') updates expected surface, appends an auditable amendment record, and re-locks Contract-SHA256.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_amend"
    expected: exit 0

- AC-03: Git pre-commit hook installation and uninstallation ('goatee hook install/uninstall') correctly registers and removes executable pre-commit hook enforcing budget compliance.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_hook"
    expected: exit 0

- AC-04: MCP server exposes 'goatee_propose_budget' allowing coding assistants to propose expected surface, reuse strategy, dependency policy, and blast radius for DRAFT contracts.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_mcp"
    expected: exit 0

- AC-05: Complete regression test suite passes 100% green with zero errors.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-06: Documentation and command help accurately reflect contract integrity, amendment workflow, Git hook guardrails, and MCP budget proposal capabilities.
  Evidence:
    type: manual
    instruction: Inspect README.md and goatee --help to verify all new integrity safeguards and commands are accurately documented.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/parser.py
- goatee/core/verifier.py
- goatee/core/status.py
- goatee/core/mcp.py
- tests/test_integrity.py
- tests/test_amend.py
- tests/test_hook.py
- tests/test_mcp.py
- tests/test_cli.py
- README.md

### Reuse Strategy
- Reuse `parse_change_contract` and `update_contract_state` in `goatee/core/parser.py`.
- Reuse `compare_budget` in `goatee/core/budget.py`.
- Reuse `resolve_or_fail_change_id` in `goatee/cli.py`.
- Standard library `hashlib`, `pathlib`, `stat`, `json`, `sys`.
- No new runtime dependencies.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/models.py
- goatee/core/budget.py
- goatee/core/attest.py
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
- .goatee/changes/008-preflight-and-agent-bridge/**

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: 2c76e60f7ed0662d39b5868a8953f05f8c39d07e
Contract-SHA256: 805ba2afd6c7d64f0991657ae32822bfcc50b7a8770c55f19ebaf671a511e32e
