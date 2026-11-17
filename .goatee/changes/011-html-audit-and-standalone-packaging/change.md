# Change: 011-html-audit-and-standalone-packaging

## Intent

Deliver presentation-ready, tamper-evident HTML delivery certificates ('goatee export --html') for client and stakeholder verification, and provide zero-dependency standalone executable packaging ('dist/goatee' via Python zipapp) to enable seamless GoATee adoption across non-Python developer ecosystems.

## Agreed Scope

### In Scope
- Implement HTML audit certificate generator ('goatee export [change-id] --html [--output <path>]' and 'goatee cert') in goatee/core/exporter.py.
- Certificate contains header metadata, dual-verdict status badges, cryptographic commit provenance, requirement coverage matrix, budget surface analysis, and signed human attestation.
- Complete standalone styling: embedded CSS, responsive layout, print/PDF optimized, zero external CDN links or network requests.
- Implement standalone executable builder in scripts/build_zipapp.py that packages the goatee application into an executable single-file zipapp (dist/goatee).
- Update goatee/cli.py with 'export' and 'cert' subparsers.
- Maintain zero external runtime dependencies (Python standard library only).
- Provide unit test coverage in tests/test_exporter.py and tests/test_zipapp.py.
- Update README.md and CLI help documentation.

### Out of Scope
- No third-party CSS or JS dependencies.
- No GitHub Actions CI workflow (Sprint 012).
- No headless browser PDF rasterization (generates print-ready HTML).

## Acceptance Criteria

- AC-01: HTML certificate generator ('goatee export --html') produces standalone, valid HTML5 with embedded CSS, containing dual-verdict status, requirement matrix, budget diffs, and cryptographic hash provenance.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_exporter"
    expected: exit 0

- AC-02: Standalone packaging builder ('scripts/build_zipapp.py') generates executable 'dist/goatee' zipapp that successfully executes CLI commands with zero installation.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_zipapp"
    expected: exit 0

- AC-03: Full regression test suite passes 100% green with zero errors across all modules.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-04: HTML export and standalone packaging capabilities are accurately documented in README.md and CLI help output.
  Evidence:
    type: manual
    instruction: Inspect README.md and goatee --help to verify documentation of export and standalone packaging.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/exporter.py
- scripts/build_zipapp.py
- tests/test_exporter.py
- tests/test_zipapp.py
- README.md

- tests/test_cli.py
### Reuse Strategy
- Standard library: html, zipapp, pathlib, hashlib, sys, argparse.
- Reuse get_change_report in goatee/core/status.py.
- Reuse parse_change_contract in goatee/core/parser.py.
- Zero external runtime dependencies.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/models.py
- goatee/core/attest.py
- goatee/core/verifier.py
- goatee/core/mcp.py
- goatee/core/budget.py
- goatee/core/linter.py
- goatee/core/rules.py
- goatee/core/lifecycle.py
- goatee/core/status.py
- goatee/core/parser.py
- .goatee/project.md
- .goatee/changes/002-handoff-budget/**
- .goatee/changes/003-verification-receipt/**
- .goatee/changes/004-proof-hardening/**
- .goatee/changes/005-control-status/**
- .goatee/changes/006-human-attestation/**
- .goatee/changes/007-release-prep/**
- .goatee/changes/008-preflight-and-agent-bridge/**
- .goatee/changes/009-contract-integrity-and-guardrails/**
- .goatee/changes/010-lifecycle-rules-and-linting/**

## Amendments
- [2026-11-17 12:06:48Z] Added surface 'tests/test_cli.py' (Attributed to Shailesh Rawat <shailesh.rawat1403@gmail.com>; Reason: Added CLI tests for export and cert commands)

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: f04713295fa2b506fb25a8b813fdec8d4ff0c7bf
Contract-SHA256: 050655f4fefbee60146c0a08f41256479e857383c80be554aa88dfb2331ca1aa
