# Change: 004-proof-hardening

## Intent

Strengthen GoATee's deterministic proof boundary by detecting persistent Git-visible repository state mutations present at criterion inspection boundaries (including .goatee/**), detecting changes to Git HEAD identity during verification, terminating timed-out commands, failing closed on Git inspection errors, and preventing malformed contracts from silently degrading into lifecycle defaults, while preserving the existing lightweight, path-based Build Budget model.

## Agreed Scope

### In Scope
- Assert HEAD identity before and after every automated criterion (`HEAD == Verified-Commit`).
- Include `.goatee/**` in per-criterion verification mutation detection.
- Fail closed on Git inspection errors across initial resolution, diff discovery, and in-flight checks.
- Bound command execution with default 120s timeout and `stdin=subprocess.DEVNULL`.
- Terminate hanging direct child processes and record explicit `TIMEOUT` diagnostics.
- Introduce `ContractSyntaxError` enforcing required sections, matching change-id directory names, and strict `DRAFT | APPROVED` states.
- Discover Git operations deterministically via `git diff --name-status --find-renames <baseline>..<target>`, preserving `A/M/D/R` operations internally.
- Add comprehensive regression tests for HEAD mutation, timeouts, control-plane halts, fail-closed CLI/verifier behavior, and mixed path separators.

### Out of Scope
- Transient filesystem tracing or monitoring within criterion execution boundaries.
- goatee status (deferred to 005-control-status).
- goatee attest and review.md (deferred to 006-human-attestation).
- Operation-specific Build Budget syntax (e.g. no `DELETE foo.py` mini-language in contracts).
- Filesystem sandboxing or monitoring of ignored files (`.gitignore`).
- Complete cross-platform descendant-process-tree cleanup.
- External parser or schema dependencies (AST, CommonMark, YAML, JSONSchema).
- Network services, background agents, or LLM integrations.

## Acceptance Criteria

- AC-01: Verifier detects when an evidence command changes Git HEAD even when the working tree is clean afterward, marking the criterion FAILED, halting later criteria, and setting verdict to NEEDS_REVIEW.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_halt_on_head_mutation
    expected: exit 0

- AC-02: Verifier asserts that HEAD equals Verified-Commit before each automated criterion executes, halting before execution if state drift occurred.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_head_invariant_before_criterion
    expected: exit 0

- AC-03: Verifier detects and halts if an evidence command modifies any file inside `.goatee/**` during execution, providing no control-plane exemption to commands.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_halt_on_control_plane_mutation
    expected: exit 0

- AC-04: Git inspection operations fail closed across initial resolution, diff discovery, and in-flight checks, halting with descriptive diagnostics.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_cli.py -k test_git_inspection_fails_closed
    expected: exit 0

- AC-05: Automated criteria execute with an enforced timeout (default 120s); timed-out direct child processes are terminated, marked FAILED with TIMEOUT diagnostic, and do not hang indefinitely.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_command_timeout_handling
    expected: exit 0

- AC-06: Verification command execution uses stdin=subprocess.DEVNULL and shell=False without relying on interactive terminal input.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py -k test_non_interactive_execution
    expected: exit 0

- AC-07: Parser raises descriptive ContractSyntaxError when required sections or metadata keys are missing, when change-id does not match the parent directory, or when states are outside DRAFT | APPROVED, while preserving DRAFT as valid lifecycle state.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_parser.py -k test_strict_contract_syntax_errors
    expected: exit 0

- AC-08: Git delta discovery uses --find-renames and preserves A/M/D/R operation details internally while Expected Surface path matching continues to evaluate normalized paths without mini-languages.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_preserves_diff_operation_semantics
    expected: exit 0

- AC-09: Existing exact-file and directory-prefix budget path matching remains 100% compatible; all Phase 2 budget tests pass.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py
    expected: exit 0

- AC-10: Existing Phase 3 verification tests pass, updated only where stricter integrity checks legitimately supersede looser v0.1 assumptions.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_verifier.py
    expected: exit 0

- AC-11: Path normalization unit tests prove mixed path separators normalize predictably for budget matching.
  Evidence:
    type: automated
    command: python3 -m unittest tests/test_budget.py -k test_path_normalization_mixed_separators
    expected: exit 0

- AC-12: Confirm pyproject.toml dependencies = [] and implementation remains Python standard library only.
  Evidence:
    type: manual
    instruction: Inspect pyproject.toml and imports across goatee/ to confirm zero external runtime dependencies.

## Build Budget

### Expected Surface
- goatee/cli.py
- goatee/core/budget.py
- goatee/core/parser.py
- goatee/core/verifier.py
- tests/test_budget.py
- tests/test_cli.py
- tests/test_parser.py
- tests/test_verifier.py

### Reuse Strategy
- Leverage Python standard library subprocess, signal, shlex, hashlib, pathlib, re.
- Reuse BuildBudget and path matching logic in budget.py.
- Reuse receipt.md template without modifications.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/templates/project.md
- goatee/templates/change.md
- goatee/templates/handoff.md
- goatee/templates/receipt.md
- Phase 1 approval semantics (goatee approve)
- Phase 2 budget comparison path matching rules
- Phase 3 clean-working-tree requirement prior to verify
- Zero third-party runtime dependencies

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: d01f69ccccf027b42661b28b0783c962b00688c6
