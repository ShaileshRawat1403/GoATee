# Change: 002-handoff-budget

## Intent

Enable an approved GoATee change contract to be handed to any external
coding agent in a consistent, bounded format, and provide the deterministic
file-level comparison primitive that Phase 3 will use to determine whether
an implementation remained within its approved Build Budget.

## Agreed Scope

### In Scope
- Render an approved change contract as a model-agnostic handoff.
- Render handoff to stdout only.
- Include project constraints, agreement, acceptance criteria, approved Build Budget, and baseline provenance.
- Compare delivered Git changes against the approved Expected Surface.
- Detect modifications inside protected Blast Radius paths.
- Distinguish expected changes from deviations.
- Ignore GoATee's own .goatee/** control artifacts when evaluating the application implementation surface.
- Return structured budget-comparison results for later use by Phase 3.

### Out of Scope
- Running tests or verification commands.
- Generating receipt.md.
- Persisting handoff output.
- Automatically approving deviations.
- Semantic code review.
- Dependency graph analysis.
- Package-manager-specific dependency inspection.
- Agent-specific prompt adapters.
- LLM/API calls.

## Acceptance Criteria

- AC-01: An approved change can be rendered as a portable handoff to stdout
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_handoff_command_success
    expected: exit 0

- AC-02: Handoff creates no persistent artifact on disk
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_handoff_creates_no_persistent_artifact
    expected: exit 0

- AC-03: Handoff refuses a DRAFT agreement or budget
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_handoff_refuses_draft
    expected: exit 0

- AC-04: Handoff refuses a change without Baseline-Commit
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_handoff_refuses_missing_baseline
    expected: exit 0

- AC-05: Budget comparison identifies files inside Expected Surface
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_files_inside_expected_surface
    expected: exit 0

- AC-06: Budget comparison reports files outside Expected Surface
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_files_outside_expected_surface
    expected: exit 0

- AC-07: Budget comparison reports modifications to Blast Radius paths
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_blast_radius_modifications
    expected: exit 0

- AC-08: .goatee/** changes do not count as application-surface deviations
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_control_artifacts_ignored
    expected: exit 0

- AC-09: Directory entries in Expected Surface match descendants
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_directory_prefix_matching
    expected: exit 0

- AC-10: Phase 2 adds zero runtime dependencies
  Evidence:
    type: manual
    instruction: Inspect pyproject.toml and imports; confirm no third-party runtime dependencies are added.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/budget.py
- goatee/templates/handoff.md
- tests/test_budget.py
- tests/test_cli.py
- README.md

### Reuse Strategy
- Existing Phase 1 ChangeContract parser
- Existing ContractState and BuildBudget structures
- Python pathlib for path normalization
- Git CLI for authoritative changed-file discovery
- Ponytail principles for implementation restraint

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/core/parser.py
- goatee/templates/project.md
- goatee/templates/change.md
- Phase 1 approval semantics (goatee approve)
- Baseline commit locking semantics
- Metadata grammar

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: a90ab3463e38946b410690b1f5116d6e91216c90
