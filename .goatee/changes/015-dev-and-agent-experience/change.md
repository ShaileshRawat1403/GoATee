# Change: 015-dev-and-agent-experience

## Intent

Streamline GoATee's developer and AI coding agent experience across everyday workflows by providing direct verification failure diagnostics, safe and non-blocking multi-criterion human attestation, actionable lifecycle next-step guidance, and enriched structured MCP responses, while strictly preserving human approval authority, dual-verdict delivery separation, receipt integrity, provenance binding, and fail-closed landing.

## Agreed Scope

### In Scope

- Workstream 1: Verification Diagnostics (`goatee verify`)
  - Default terminal failure context in `goatee verify`: On `verdict == "FAIL"`, default terminal output displays actionable metadata (failing criterion ID, evidence type, declared command, expected vs observed exit status) without dumping raw command stdout/stderr.
  - Explicit diagnostic detail option: Detailed command output excerpts are emitted to console only when an explicit `--diagnostics` (or `-d`) flag is passed to `goatee verify`. Detailed excerpts are strictly bounded by both line limit (maximum 15 lines) and byte limit (maximum 2048 bytes). Document that truncation mitigates terminal flooding but does not guarantee automated secret sanitization.
  - JSON output purity: Preserve clean JSON formatting on stdout when `--json` is supplied, keeping diagnostics within structured JSON fields or sending human messages to stderr.
  - Actionable pre-flight guidance: When `goatee verify` halts on uncommitted files, output explicit guidance directing the user or agent to `goatee check <change-id>` for uncommitted pre-flight budget inspection.
  - Limit terminal failure diagnostics changes strictly to `goatee verify`, leaving `goatee ci` behavior and reports intact without modifying `goatee/core/ci.py`.

- Workstream 2: Safe Multi-Criterion Human Attestation UX (`goatee attest`)
  - Enhance usability and test coverage for the existing `run_interactive_attestation()` wizard, which validates clean repo state at invocation start, collects explicit human decisions in memory, and writes `review.md` in a single atomic file write.
  - Provide an explicit non-interactive multi-criterion attestation mechanism via repeatable `--decision <criterion_id>=<VERIFIED|REJECTED>:"<note>"` parameter, requiring an individual decision and single-line note for every attested criterion.
  - Validate all supplied decisions upfront before mutating repository state; reject duplicate or conflicting criterion entries fail-closed with zero partial file mutation.
  - Human authorization boundary: Positive non-interactive attestation requires explicit human authorization outside the command itself. Git-configured identity establishes reviewer attribution for audit and provenance, not independent cryptographic human authentication. A CLI invocation cannot technically prove human intent, and environment-variable checks alone do not prevent autonomous agent execution; rather, human review policies govern valid attestation authority.
  - Preserve all repository clean-worktree checks, committed-receipt prerequisites, receipt SHA256 binding, and event replay validation without relaxation.

- Workstream 3: Lifecycle Next-Action Guidance (`goatee status` & MCP)
  - Implement deterministic next-action resolution in `goatee/core/status.py` distinguishing:
    - Human Gates (`DRAFT` baseline locking; `NEEDS_REVIEW` manual attestation; PR review).
    - Machine Actions (Implementation in progress; pre-flight checking; candidate verification).
    - Blocked States (Machine verification failures; budget deviations; human rejections).
    - Completed States (Landed/archived changes).
  - Surface `Next Action:` recommendation in `goatee status <change-id>` detail view and concise `Next:` hints in `goatee status` project overview.
  - Expose structured `next_action` string through MCP `goatee_get_contract` and status helpers without introducing `goatee status --json`.
  - Ensure safe handling of multiple concurrent changes and trunk branch checkouts, prompting explicitly for `<change-id>` disambiguation rather than guessing.

- Workstream 4: MCP Readability and Consistency (`goatee/core/mcp.py`)
  - Structured budget comparison: Return machine-readable JSON payload (`expected_changes`, `surface_deviations`, `protected_changes`, `passed`) alongside backward-compatible plain text in `goatee_check_budget`.
  - Enriched contract handoff: Include `project_constraints` (from `.goatee/project.md`), the 5 canonical implementation rules (from `handoff.md`), and the computed `next_action` in `goatee_get_contract`.
  - Standardize error formatting with actionable `recovery_suggestion` hints across MCP tool handlers.
  - Avoid introducing broad write-capable or lifecycle-advancing tools (e.g. no MCP approve, attest, or land).

- Negative Security & Governance Tests
  - SEC-01: Prohibit positive `VERIFIED` attestation for automated criteria across both interactive and batch CLI attestation.
  - SEC-02: Require uncommitted application files and uncommitted `receipt.md` to block attestation fail-closed.
  - SEC-03: Reject malformed, duplicate, or conflicting `--decision` entries with zero partial state mutation.
  - SEC-04: Prevent trunk `goatee status` next-action resolution from guessing active changes when multiple candidates exist.
  - SEC-05: Ensure machine verification failures (`verdict == "FAIL"`) report `Blocked State` and never suggest human attestation or landing.

### Out of Scope

- Blanket `--all-manual` attestation without individual criterion decisions.
- Programmatic or flag-based contract approval (`goatee approve --all` deferred until human authority semantics are designed).
- Automatic Git commits, branch switching, or automated landing.
- Modifying `goatee/core/ci.py` or altering GitHub Actions CI markdown report formats.
- Introducing a `goatee status --json` CLI flag.
- Autonomous agent origination of human review authority.
- Modifying or re-verifying historical landed change records in `.goatee/archive/`.
- Sprint 016 BA/PM collaboration and requirements elicitation features.

## Acceptance Criteria

- AC-01: `goatee verify` outputs actionable failure metadata by default and bounded output excerpts only when `--diagnostics` is enabled
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_verify_failure_diagnostics_console
    expected: exit 0

- AC-02: Pre-flight budget check guidance displayed when `goatee verify` halts on uncommitted working tree
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_verify_dirty_worktree_guidance
    expected: exit 0

- AC-03: Interactive attestation wizard collects multiple criterion decisions in memory and writes `review.md` atomically
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_interactive_attestation_multi_criterion
    expected: exit 0

- AC-04: Non-interactive batch attestation processes explicit `--decision` entries with upfront validation and zero partial mutation
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_batch_attestation_explicit_decisions
    expected: exit 0

- AC-05: Attestation security controls reject automated criterion verification, uncommitted receipts, and malformed decisions
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_attest.py -k test_attestation_security_regressions
    expected: exit 0

- AC-06: Lifecycle next-action guidance resolves accurate state-machine recommendations across all change phases and disambiguates trunk
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_status.py -k test_lifecycle_next_action_guidance
    expected: exit 0

- AC-07: MCP tools provide backward-compatible structured budget results and enriched contract handoff rules
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_mcp.py -k test_mcp_structured_budget_and_contract
    expected: exit 0

- AC-08: Full GoATee test suite passes with zero regressions
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-09: Manual inspection of verification diagnostics, attestation workflows, lifecycle guidance, and MCP consistency
  Evidence:
    type: manual
    instruction: Inspect goatee verify console failure output, test interactive and explicit batch attestation, verify status next-action hints, and review MCP structured tool outputs to confirm enhanced ergonomics and preserved security boundaries.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/attest.py
- goatee/core/mcp.py
- goatee/core/status.py
- goatee/core/verifier.py
- tests/test_attest.py
- tests/test_cli.py
- tests/test_mcp.py
- tests/test_status.py
- tests/test_verifier.py

### Reuse Strategy
- Reuse `bounded_excerpt()` from `goatee.core.verifier` for terminal failure diagnostic truncation.
- Reuse `run_interactive_attestation()`, `verify_receipt_prerequisites()`, `derive_review_state()`, and `format_review_markdown()` from `goatee.core.attest`.
- Reuse `get_change_report()`, `get_project_status()`, and `resolve_active_change_id()` from `goatee.core.status`.
- Reuse `compare_budget()` and `BudgetComparisonResult` from `goatee.core.budget`.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- .goatee/archive/
- .github/workflows/
- goatee/core/ci.py
- goatee/core/lifecycle.py
- goatee/core/parser.py
- goatee/core/budget.py

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: ca6a536541c06d3587a858e98691efd6e784b949
Contract-SHA256: 29158a92664298e36d047a378ae95cee19e2b1abe4f51d4c77d6928820a6ee2b
