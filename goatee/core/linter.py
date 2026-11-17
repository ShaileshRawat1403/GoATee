"""Semantic and structural contract linter for GoATee change contracts."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from goatee.core.budget import matches_path_entry, normalize_relpath
from goatee.core.parser import parse_change_contract, parse_metadata_block, ContractSyntaxError
from goatee.core.models import ChangeContract


@dataclass(frozen=True)
class LintIssue:
    severity: str  # "ERROR" | "WARNING"
    section: str
    message: str


def check_path_safety(path_str: str) -> Optional[str]:
    """Check if a path entry violates security boundaries (traversal, absolute path)."""
    norm = path_str.strip().replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[a-zA-Z]:", norm):
        return f"Absolute path '{path_str}' is forbidden. All budget paths must be relative to project root."
    parts = norm.split("/")
    if ".." in parts:
        return f"Directory traversal '{path_str}' is forbidden."
    if any(c in path_str for c in ["\0", "\r", "\n"]):
        return f"Path '{path_str}' contains forbidden control characters."
    return None


def lint_change_contract(contract_path: Path) -> List[LintIssue]:
    """Inspect a change.md file for syntax errors, budget conflicts, and security violations.
    
    Zero side-effects. Returns a list of LintIssue objects.
    """
    issues: List[LintIssue] = []

    if not contract_path.is_file():
        issues.append(LintIssue(
            severity="ERROR",
            section="File",
            message=f"Change contract file not found at {contract_path}"
        ))
        return issues

    try:
        content = contract_path.read_text(encoding="utf-8")
    except Exception as e:
        issues.append(LintIssue(
            severity="ERROR",
            section="File",
            message=f"Failed to read contract as UTF-8: {e}"
        ))
        return issues

    # 1. Structural section checks
    required_sections = [
        ("## Intent", "Missing '## Intent' section."),
        ("## Agreed Scope", "Missing '## Agreed Scope' section."),
        ("## Acceptance Criteria", "Missing '## Acceptance Criteria' section."),
        ("## Build Budget", "Missing '## Build Budget' section."),
        ("## Contract State", "Missing '## Contract State' section."),
    ]
    for section_header, msg in required_sections:
        if section_header not in content:
            issues.append(LintIssue(severity="ERROR", section="Structure", message=msg))

    if "### In Scope" not in content and "## Agreed Scope" in content:
        issues.append(LintIssue(severity="WARNING", section="Scope", message="Agreed Scope missing '### In Scope' subsection."))
    if "### Out of Scope" not in content and "## Agreed Scope" in content:
        issues.append(LintIssue(severity="WARNING", section="Scope", message="Agreed Scope missing '### Out of Scope' subsection."))

    # 2. Try parsing the contract model
    contract: Optional[ChangeContract] = None
    try:
        contract = parse_change_contract(contract_path)
    except ContractSyntaxError as cse:
        issues.append(LintIssue(severity="ERROR", section="Syntax", message=str(cse)))
    except Exception as e:
        issues.append(LintIssue(severity="ERROR", section="Syntax", message=f"Unexpected parsing error: {e}"))

    if not contract:
        return issues

    # 3. Intent checks
    if not contract.intent.strip():
        issues.append(LintIssue(severity="ERROR", section="Intent", message="Intent cannot be empty."))

    # 4. Acceptance criteria checks
    if not contract.acceptance_criteria:
        issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message="At least one acceptance criterion is required."))
    else:
        seen_ids: Set[str] = set()
        for ac in contract.acceptance_criteria:
            if not ac.id:
                issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message="Criterion missing identifier."))
                continue
            if ac.id in seen_ids:
                issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message=f"Duplicate criterion ID '{ac.id}'."))
            seen_ids.add(ac.id)

            if not re.match(r"^AC-\d+$", ac.id):
                issues.append(LintIssue(severity="WARNING", section="Acceptance Criteria", message=f"Criterion ID '{ac.id}' should follow standard convention 'AC-XX'."))

            if not ac.description.strip():
                issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message=f"Criterion '{ac.id}' has empty description."))

            if not ac.evidence_type:
                issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message=f"Criterion '{ac.id}' missing evidence type."))
            elif ac.evidence_type not in ("automated", "manual"):
                issues.append(LintIssue(
                    severity="ERROR",
                    section="Acceptance Criteria",
                    message=f"Criterion '{ac.id}' has invalid evidence type '{ac.evidence_type}'. Must be 'automated' or 'manual'."
                ))

            if ac.evidence_type == "automated":
                if not ac.command or not ac.command.strip():
                    issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message=f"Automated criterion '{ac.id}' missing required execution command."))
                if not ac.expected or not ac.expected.strip():
                    issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message=f"Automated criterion '{ac.id}' missing required expected exit status."))
            elif ac.evidence_type == "manual":
                if not ac.instruction or not ac.instruction.strip():
                    issues.append(LintIssue(severity="ERROR", section="Acceptance Criteria", message=f"Manual criterion '{ac.id}' missing required human inspection instruction."))

    # 5. Build Budget checks
    expected_surface = contract.build_budget.expected_surface
    blast_radius = contract.build_budget.blast_radius

    if not expected_surface:
        issues.append(LintIssue(severity="WARNING", section="Build Budget", message="Expected Surface is empty. Implementation changes will all be flagged as surface deviations."))

    # Path safety
    for p in expected_surface:
        err = check_path_safety(p)
        if err:
            issues.append(LintIssue(severity="ERROR", section="Build Budget", message=f"Expected Surface: {err}"))

    for p in blast_radius:
        err = check_path_safety(p)
        if err:
            issues.append(LintIssue(severity="ERROR", section="Build Budget", message=f"Blast Radius: {err}"))

    # Overlap / Conflict detection: Expected Surface vs Blast Radius
    for exp in expected_surface:
        for br in blast_radius:
            if matches_path_entry(exp, br) or matches_path_entry(br, exp):
                issues.append(LintIssue(
                    severity="ERROR",
                    section="Build Budget",
                    message=(
                        f"FATAL CONFLICT: Expected Surface path '{exp}' overlaps with protected Blast Radius '{br}'. "
                        "A path cannot be simultaneously expected to change and protected against change."
                    )
                ))

    # 6. Contract State checks
    state = contract.contract_state
    if state.agreement_status not in ("DRAFT", "APPROVED", "REJECTED"):
        issues.append(LintIssue(
            severity="ERROR",
            section="Contract State",
            message=f"Invalid Agreement-Status '{state.agreement_status}'. Must be DRAFT, APPROVED, or REJECTED."
        ))

    if state.budget_status not in ("DRAFT", "APPROVED", "REJECTED"):
        issues.append(LintIssue(
            severity="ERROR",
            section="Contract State",
            message=f"Invalid Budget-Status '{state.budget_status}'. Must be DRAFT, APPROVED, or REJECTED."
        ))

    if state.agreement_status == "APPROVED" and not state.baseline_commit:
        issues.append(LintIssue(
            severity="WARNING",
            section="Contract State",
            message="Agreement-Status is APPROVED but Baseline-Commit is missing. Run 'goatee approve' to lock baseline."
        ))

    return issues


def format_lint_report(change_id: str, issues: List[LintIssue]) -> str:
    """Format lint issues into human-readable report."""
    errors = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]

    lines = [
        f"# GoATee Contract Lint Report: {change_id}",
        f"Status: {'FAILED' if errors else ('PASSED_WITH_WARNINGS' if warnings else 'PASSED')}",
        f"Errors: {len(errors)}, Warnings: {len(warnings)}",
        ""
    ]

    if not issues:
        lines.append("✓ Contract is structurally and semantically clean. Zero issues detected.")
        return "\n".join(lines)

    if errors:
        lines.append("## Errors (Must be resolved before approval):")
        for err in errors:
            lines.append(f"  - [{err.section}] {err.message}")
        lines.append("")

    if warnings:
        lines.append("## Warnings (Advisory):")
        for w in warnings:
            lines.append(f"  - [{w.section}] {w.message}")
        lines.append("")

    return "\n".join(lines).rstrip()
