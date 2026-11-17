# Change: 012-ci-cd-and-team-governance

## Intent

Equip GoATee with production-ready CI/CD integration, team governance automation, and expanded agent MCP intelligence: provide 'goatee ci' with automatic GitHub Actions step summary generation, flexible PR validation policies ('--allow-needs-review'), GitHub/GitLab PR comment export ('goatee export --pr-comment'), official GitHub Actions workflow templates, repository hygiene ignores, and contract linting via the MCP server.

## Agreed Scope

### In Scope
- Core CI Engine: Implement 'goatee.core.ci' with zero-dependency CI runner capable of contract linting, tamper verification, test execution, receipt compilation, and status evaluation.
- GitHub Actions Integration: Automatic detection and writing to '$GITHUB_STEP_SUMMARY' with structured markdown badges, tables, and diagnostics.
- CLI CI Commands: Implement 'goatee ci' and enhance 'goatee verify --ci' with '--allow-needs-review' and '--json' output options.
- PR Comment Export: Implement 'goatee export --pr-comment' (and '--markdown') in 'goatee.core.exporter' and 'goatee/cli.py'.
- Official CI Workflow: Create '.github/workflows/goatee-verify.yml' validating push and pull requests with GoATee CI and HTML certificate upload.
- MCP Server Expansion: Add 'goatee_lint_contract' tool to 'goatee/core/mcp.py' allowing agents to validate contracts before approval.
- Repository Hygiene: Update '.gitignore' to ignore '.goatee/.active' and '.goatee/certificates/'.
- Unit Testing: Thorough unit tests in 'tests/test_ci.py', 'tests/test_exporter.py', and 'tests/test_mcp.py'.
- Documentation: Complete documentation in 'README.md'.

### Out of Scope
- Direct GitHub API token authentication or auto-posting comments via remote REST calls (keeps GoATee dependency-free; CI runner uses workflow action or summary environment).
- External database integrations.
- Non-Python runtime runners.

## Acceptance Criteria

- AC-01: CI engine executes change verification, respects '--allow-needs-review', formats JSON output, writes to '$GITHUB_STEP_SUMMARY', and exits cleanly.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_ci"
    expected: exit 0

- AC-02: Exporter generates GitHub-ready PR comment markdown summaries via 'goatee export --pr-comment'.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_exporter"
    expected: exit 0

- AC-03: MCP server provides 'goatee_lint_contract' tool that accurately surfaces contract syntax and semantic issues.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests -k "test_mcp"
    expected: exit 0

- AC-04: Full regression test suite passes 100% green with zero errors across all modules.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-05: Developer and CI workflow documentation is comprehensive, clear, and actionable in README.md and .github/workflows/goatee-verify.yml.
  Evidence:
    type: manual
    instruction: Inspect README.md and .github/workflows/goatee-verify.yml to verify CI/CD usage instructions and workflow validity.

## Build Budget

### Expected Surface
- .github/workflows/goatee-verify.yml
- .gitignore
- README.md
- goatee/cli.py
- goatee/core/ci.py
- goatee/core/exporter.py
- goatee/core/mcp.py
- tests/test_ci.py
- tests/test_exporter.py
- tests/test_mcp.py

### Reuse Strategy
- goatee.core.verifier.verify_change for execution and receipt compilation
- goatee.core.linter.lint_change_contract for semantic and structural contract linting
- goatee.core.parser.compute_contract_hash and read_contract_hash for tamper protection
- goatee.core.status.resolve_active_change_id for repository context resolution
- goatee.core.exporter for formatted certificate and markdown generation

Dependency-Policy: No new runtime dependencies

### Blast Radius
- .goatee/changes/008-preflight-and-agent-bridge/**
- .goatee/changes/009-contract-integrity-and-guardrails/**
- .goatee/changes/010-lifecycle-rules-and-linting/**
- .goatee/changes/011-html-audit-and-standalone-packaging/**

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: c745dd6a8c9de9119cd009ae07073b8deff5b169
Contract-SHA256: 9db0304204ef520cb45a33c281c319807cc54d5d4dcee8d6f9b952a60d1bc47d
