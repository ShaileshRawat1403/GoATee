# THIRD_PARTY.md

This document records the provenance, licenses, and specific elements learned, adapted, or referenced from external open-source projects for GoATee v0.1.

GoATee follows the **BUILD THE DIFFERENCE** engineering rule:
> *Search existing solutions before creating one. Reuse the smallest relevant unit. Do not import an entire framework to obtain one capability. Preserve attribution and record provenance.*

---

## 1. github/spec-kit

- **Repository**: [github/spec-kit](https://github.com/github/spec-kit)
- **License**: MIT (`Copyright (c) GitHub, Inc.`)
- **Version Reference**: `v1.0.6` (tagged release)
- **Nature of Reuse**: **Adapted text / structure**
- **What GoATee Adapted**:
  - Concept of project-level constitution / rules adapted into `project.md` permanent constraints.
  - User story acceptance scenario patterns (Given / When / Then) adapted into `change.md` structured acceptance criteria (`AC-xx`).
- **What GoATee Changed**:
  - Eliminated complex multi-file planning trees (`.specify/`, `specs/<feature>/`, `tasks.md`, `checklists/`) in favor of a single self-contained contract (`change.md`).
  - Added explicit dual evidence typing (`automated` vs. `manual`) directly to each acceptance criterion.

---

## 2. Fission-AI/OpenSpec

- **Repository**: [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec)
- **License**: MIT
- **Version Reference**: `v1.13.1` (tagged release)
- **Nature of Reuse**: **Adapted text / directory conventions**
- **What GoATee Adapted**:
  - The `changes/<change-id>/` directory convention for brownfield change isolation.
  - High-level change proposal structure (Intent and Scope).
- **What GoATee Changed**:
  - Collapsed OpenSpec's multi-document workflow (`proposal.md`, `design.md`, `tasks.md`, delta specs) into a unified contract (`change.md`) and delivery snapshot (`receipt.md`).
  - Replaced TypeScript CLI runtime with zero-dependency Python stdlib.

---

## 3. DietrichGebert/ponytail

- **Repository**: [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)
- **License**: MIT
- **Version Reference**: `v4.10.0` (tagged release)
- **Nature of Reuse**: **Adapted principles / policy**
- **What GoATee Adapted**:
  - The 7-step minimality ladder and core dependency restraint philosophy (*"write less code"*, *"prefer stdlib and existing codebase"*).
- **What GoATee Changed**:
  - Converted loose prompt instructions into a formal, proportionate **Build Budget** section (`Expected Surface`, `Reuse Strategy`, `Dependency Policy`, `Blast Radius`) with agent proposal and human approval.

---

## 4. liatrio-labs/spec-driven-workflow

- **Repository**: [liatrio-labs/spec-driven-workflow](https://github.com/liatrio-labs/spec-driven-workflow)
- **License**: Apache-2.0
- **Version Reference**: Commit `930d6cb` on `main` branch (skill `sdd`)
- **Nature of Reuse**: **Adapted concepts**
- **What GoATee Adapted**:
  - Requirement-to-evidence pairing and the Coverage Matrix pattern linking requirements to concrete proof artifacts.
- **What GoATee Changed**:
  - Automated the declared-vs-observed matching process into GoATee's deterministic verifier and Markdown delivery snapshot (`receipt.md`).

---

## 5. bmad-code-org/BMAD-METHOD

- **Repository**: [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD)
- **License**: MIT
- **Version Reference**: Commit `374719c` on `main` branch
- **Nature of Reuse**: **Conceptual influence only**
- **What GoATee Learned**:
  - Right-sizing process ceremony to match change complexity, ensuring v0.1 remains lightweight without unnecessary state-machine overhead.
- **What GoATee Reused/Copied**: None (no text, templates, or code copied).
