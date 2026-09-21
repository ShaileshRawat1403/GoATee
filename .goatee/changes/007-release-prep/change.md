# Change: 007-release-prep

## Intent

Prepare the completed GoATee v0.2 implementation for release by aligning package version metadata, the project initialization template, and public README documentation with the capabilities already delivered and verified in 004-proof-hardening, 005-control-status, and 006-human-attestation.

## Agreed Scope

### In Scope
- Update pyproject.toml package version from 0.1.0 to 0.2.0.
- Update goatee/templates/project.md so newly initialized projects recognize review.md when human attestation exists.
- Add the permanent project constraint that human attestation must never modify receipt.md.
- Update README.md to accurately describe the current v0.2 artifact model, command surface, verification hardening, control-status inspection, human attestation, dual-verdict model, and current 56-test state.
- Remove or rewrite stale current-state README language that incorrectly describes implemented functionality as future work or the current product as v0.1.

### Out of Scope
- No application implementation changes.
- No test implementation changes.
- No verifier, parser, budget, status, attest, model, or CLI behavior changes.
- No receipt schema changes.
- No change.md or handoff.md template changes.
- No edits to existing historical change contracts, receipts, or reviews.
- No retroactive human attestation of 003, 004, or 005.
- No dependency additions.
- No THIRD_PARTY.md changes.
- No release tag.
- No remote push.
- No CHANGELOG or new release-document file.

## Acceptance Criteria

- AC-01: Package metadata declares GoATee version 0.2.0 while retaining Python >=3.10 and zero runtime dependencies.
  Evidence:
    type: automated
    command: python3 -c "from pathlib import Path; import re; t=Path('pyproject.toml').read_text(); assert re.search(r'(?m)^version = \"0\\.2\\.0\"$', t); assert re.search(r'(?m)^requires-python = \">=3\\.10\"$', t); assert re.search(r'(?m)^dependencies = \\[\\]$', t)"
    expected: exit 0

- AC-02: The project template includes review.md as an optional human-attestation artifact and states that human attestation must never modify receipt.md.
  Evidence:
    type: automated
    command: python3 -c "from pathlib import Path; t=Path('goatee/templates/project.md').read_text(); assert 'review.md' in t; assert 'Human attestation must never modify receipt.md.' in t"
    expected: exit 0

- AC-03: README reflects v0.2, the four-artifact model, status and attest commands, dual-verdict terminology, and the current 56-test state without the known stale current-state statements identified by the release audit.
  Evidence:
    type: automated
    command: python3 -c "from pathlib import Path; t=Path('README.md').read_text(); req=['version: 0.2.0','review.md','goatee status','goatee attest','Machine Verdict','Human Review','56 tests']; assert all(x in t for x in req); stale=['This arrives with verification in a later v0.1 phase.','28 tests','GoATee v0.1 complete!']; assert all(x not in t for x in stale)"
    expected: exit 0

- AC-04: Complete regression suite remains green after release-preparation edits.
  Evidence:
    type: automated
    command: python3 -m unittest discover tests
    expected: exit 0

- AC-05: Release-facing documentation and metadata accurately describe shipped v0.2 behavior without rewriting historical evidence or overstating trust guarantees.
  Evidence:
    type: manual
    instruction: Inspect README.md, goatee/templates/project.md, and pyproject.toml for factual v0.2 release accuracy, preservation of historical provenance, correct dual-verdict terminology, and absence of trust overclaims.

## Build Budget

### Expected Surface
- README.md
- goatee/templates/project.md
- pyproject.toml

### Reuse Strategy
- Reuse the existing README structure rather than rewriting the document from scratch.
- Reuse the existing project.md template and update only the permanent artifact/invariant language required by v0.2.
- Reuse existing package metadata and change only the release version.
- No new files, libraries, schemas, or implementation abstractions.

Dependency-Policy: No new runtime dependencies

### Blast Radius
- goatee/cli.py
- goatee/core/**
- goatee/templates/change.md
- goatee/templates/handoff.md
- goatee/templates/receipt.md
- tests/**
- THIRD_PARTY.md
- .goatee/project.md
- .goatee/changes/002-handoff-budget/**
- .goatee/changes/003-verification-receipt/**
- .goatee/changes/004-proof-hardening/**
- .goatee/changes/005-control-status/**
- .goatee/changes/006-human-attestation/**

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: c1ed6bc3fbf39ead93a0012b0300be6acc63790c
