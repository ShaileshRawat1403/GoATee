---
title: GoATee
archetype: project-readme
status: active-development
owner: Shailesh Rawat
maintainer: Shailesh Rawat
version: 0.1.0
tags:
  - spec-driven-development
  - ai-assisted-development
  - forward-deployed
  - governance
  - evidence
last_reviewed: 2026-09-20
---

# GoATee

<p align="center">
  <img src="assets/goatee-mascot.png" alt="GoATee mascot" width="420">
</p>

<p align="center">
  <strong>Agree exactly. Build minimally. Prove delivery.</strong>
</p>

<p align="center">
  A lightweight control layer for AI-assisted software changes.
</p>

---

You tell an AI coding agent:

> Add CSV export to the customer report.

It starts building.

A few minutes later:

- six files changed
- a new package appeared
- an existing utility was rewritten
- something outside the request got refactored
- the agent says everything works

Maybe all of that was necessary.

Maybe none of it was.

The problem is that the agreement was never explicit.

## GoATee starts there.

Before implementation, GoATee helps establish:

```text
What are we actually changing?

What counts as done?

What should remain untouched?

What is the smallest reasonable implementation?

How will we prove delivery?
```

Then the coding agent can build.

GoATee keeps the contract.

---

## What GoATee is

GoATee is a **spec and delivery control layer** around coding assistants such as Claude Code, Codex, Gemini, Cursor, or other CLI agents.

It is designed primarily for:

* Forward Deployed Consultants
* technical product owners
* solution consultants
* AI-native builders
* non-developers directing coding agents

GoATee does **not** try to become another coding agent.

The agent reasons and writes code.

GoATee keeps track of what was agreed.

```text
HUMAN INTENT
     │
     ▼
AGREEMENT
     │
     ▼
AGENT INSPECTS REPOSITORY
     │
     ▼
PROPOSED BUILD BUDGET
     │
     ▼
HUMAN APPROVAL
     │
     ▼
BASELINE LOCKED
     │
     ▼
AI IMPLEMENTS
     │
     ▼
EVIDENCE
     │
     ▼
DELIVERY
```

---

## The three questions

Everything in GoATee exists to answer three questions.

### 1. What exactly did we agree to build?

The change contract records:

* intent
* scope
* exclusions
* acceptance criteria
* evidence requirements

### 2. How little should we build?

The coding agent inspects the repository and proposes a **Build Budget**:

* expected change surface
* existing components to reuse
* dependency policy
* protected areas
* likely blast radius

The human approves it before implementation.

Minimal does not mean code golf.

It means **proportionate to the problem**.

### 3. Can we prove what was delivered?

GoATee is being built to compare:

```text
AGREED STATE
     ↓
ACTUAL CHANGE
     ↓
OBSERVED EVIDENCE
```

The end goal is a delivery receipt grounded in repository state, tests, observations, and explicit deviations.

Not:

> "The agent said it works."

---

## The I-7 Operating Model

Behind GoATee's triad is the **I-7 Operating Model**—GoATee's human-centered change-control loop preventing AI coding agents from jumping from a vague request directly into unbounded implementation:

```text
AGREE EXACTLY
  I-1 Intent     What are we actually trying to accomplish? (Business/user need)
  I-2 Inform     What context, repo structure, and OSS exist? (Reality inspection)
  I-3 Interpret  What are in/out-of-scope and the exact acceptance criteria?

BUILD MINIMALLY
  I-4 Initiate   Agent inspects repository and proposes the technical Build Budget
  I-5 Inspect    HARD HUMAN GATE: Review & approve budget -> lock baseline commit
  I-6 Intervene  Agent implements strictly within approved boundary; deviations loop back to I-5

PROVE DELIVERY
  I-7 Iterate    Verify declared evidence against actual diff & test results -> delivery receipt
```

---

## The artifact model

GoATee deliberately keeps its persistent state small.

```text
.goatee/
├── project.md
└── changes/
    └── <change-id>/
        ├── change.md
        └── receipt.md
```

### `project.md`

Long-lived project rules.

```text
stack
verification commands
dependency policy
permanent constraints
```

### `change.md`

The agreement for one software change.

```text
intent
scope
acceptance criteria
build budget
approval state
baseline commit
```

### `receipt.md`

The evidence-backed delivery snapshot.

This arrives with verification in a later v0.1 phase.

---

## Current status

GoATee is under active development.

### Phase 1 ✓

The foundation is working:
`goatee init`, `goatee new <change-id>`, `goatee approve <change-id>`.

### Phase 2 ✓

Bounded handoff and minimality comparison are working:
`goatee handoff <change-id>` and deterministic Build Budget comparison.

Phase 2 provides:
* portable, model-agnostic handoff rendered strictly to stdout
* zero persistent artifacts created by handoff
* strict gate requiring `APPROVED` statuses and locked `Baseline-Commit`
* deterministic changed-file comparison against approved `Expected Surface`
* detection of touches to protected `Blast Radius` paths
* recursive directory prefix matching without glob mini-languages
* complete isolation of `.goatee/**` control plane from delivery surface
* zero external runtime dependencies

Current test result:

```text
20 tests
20 passed
0 errors
```

### Next

```text
Phase 3
Evidence verification
Deviation tracking
Delivery receipt (receipt.md)
Dogfooding completion
```

---

## Install

GoATee currently requires Python 3.10 or newer.

```bash
git clone <goatee-repository>
cd GoATee

pip install -e .
```

No external runtime dependencies are required.

Check the CLI:

```bash
goatee --help
```

---

## Start a project

From inside an existing Git repository:

```bash
goatee init
```

This creates:

```text
.goatee/project.md
```

Use it to describe the project environment and permanent delivery constraints.

---

## Start a change

```bash
goatee new 001-export-csv
```

GoATee creates:

```text
.goatee/changes/001-export-csv/change.md
```

The human defines the business side first:

```markdown
## Intent

Allow users to export the currently filtered report as CSV.

## In Scope

- Export visible report data.
- Preserve active filters.

## Out of Scope

- Scheduled exports.
- PDF export.
- Changes to authentication.
```

Acceptance criteria are explicit:

```markdown
- AC-01: User can export filtered records as CSV.
  Evidence:
    type: automated
    command: pytest tests/test_export.py
    expected: exit 0

- AC-02: Export control appears beside Filters.
  Evidence:
    type: manual
    instruction: Confirm Export appears beside Filters.
```

---

## Let the agent inspect before it builds

The coding agent investigates the repository and proposes the technical boundary.

Example:

```markdown
## Build Budget

### Expected Surface

- src/export/
- tests/test_export.py

### Reuse Strategy

- Existing report filtering pipeline.
- Existing CSV utility.

Dependency-Policy: No new runtime dependencies

### Blast Radius

- src/auth/
- src/billing/
```

The important part:

**The non-developer does not need to guess the architecture.**

The agent proposes.

The human approves.

---

## Lock the agreement

Once both the agreement and Build Budget are approved:

```bash
goatee approve 001-export-csv
```

GoATee checks that:

* the repository is valid
* Git has an existing `HEAD`
* the agreement is approved
* the Build Budget is approved
* unrelated application changes are not already sitting in the workspace
* the change has not already been baselined

It then records:

```text
Baseline-Commit: <git-sha>
```

That SHA becomes the reference point for the delivery.

The build can now begin.

---

## Why the baseline matters

Without a baseline:

```text
"Here is what the agent changed."
```

is surprisingly hard to define.

With one:

```text
baseline commit
      │
      │ implementation
      ▼
delivered commit
```

GoATee can eventually evaluate the actual delivery using Git rather than relying on an agent's summary.

```bash
git diff <baseline>..<delivered>
```

Repository state becomes evidence.

---

## Build the difference

GoATee itself follows the same principle it asks of coding agents:

> **Reuse intelligently. Build only what GoATee uniquely needs.**

The project learns from existing open-source work instead of rebuilding established ideas.

| Project         | GoATee learns from                                |
| --------------- | ------------------------------------------------- |
| GitHub Spec Kit | specification quality and ambiguity reduction     |
| OpenSpec        | change-oriented specification workflows           |
| Ponytail        | implementation restraint and reuse-first thinking |
| Liatrio SDD     | requirement-to-evidence mapping                   |
| BMAD Method     | right-sizing process to change complexity         |
| Git             | authoritative repository state and change history |

GoATee does not bundle these projects together.

It adapts useful primitives and builds the missing layer around them.

See [`THIRD_PARTY.md`](THIRD_PARTY.md) for exact provenance, versions, licenses, and adaptations.

---

## Five engineering principles

### BUILD THE DIFFERENCE

Search before creating.

### ARTIFACTS OVER ASSERTIONS

Repository state and evidence outrank agent narration.

### SPEC BEFORE IMPLEMENTATION

Material ambiguity is resolved before the build begins.

### PROPORTIONATE CHANGE

Change only what is necessary to satisfy the agreement safely.

### HUMAN OWNS DEVIATION

An AI can discover that scope needs to change.

It cannot silently redefine the agreement.

---

## What GoATee is not

GoATee v0.1 is intentionally narrow.

It is not:

* an AI coding model
* an agent swarm
* a model router
* an IDE
* a code editor
* an autonomous product manager
* a replacement for Git
* a heavyweight PRD framework
* an excuse to create more process

The goal is much smaller:

> Establish the agreement.
> Bound the implementation.
> Make delivery provable.

---

## Thesis

GoATee v0.1 is an experiment.

It needs to prove that someone who does not write production code can still answer:

```text
What did we agree to build?

What was the starting point?

What was the smallest reasonable implementation?

What actually changed?

Did we stay within the agreed boundary?

Can we prove the result?
```

If GoATee cannot make those answers clearer, it does not need more features.

It needs less.

---

## License

MIT.

See [`THIRD_PARTY.md`](THIRD_PARTY.md) for upstream attribution and open-source provenance.
