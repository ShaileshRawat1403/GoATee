"""Native AI agent rules scaffolding (CLAUDE.md, .cursorrules, .windsurfrules, .gemini/rules)."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from goatee.core.parser import parse_change_contract, ContractSyntaxError
from goatee.core.status import resolve_active_change_id

MARKER_START = "<!-- GOATEE-RULES-START -->"
MARKER_END = "<!-- GOATEE-RULES-END -->"

SUPPORTED_TARGETS: Dict[str, str] = {
    "claude": "CLAUDE.md",
    "cursor": ".cursorrules",
    "windsurf": ".windsurfrules",
    "gemini": ".gemini/rules",
}


def generate_rules_block(root: Path, active_change_id: Optional[str] = None) -> str:
    """Generate the GoATee agent rule block with active boundaries."""
    change_id = active_change_id or resolve_active_change_id(root)
    contract = None
    if change_id:
        c_path = root / ".goatee" / "changes" / change_id / "change.md"
        if c_path.is_file():
            try:
                contract = parse_change_contract(c_path)
            except Exception:
                contract = None

    lines = [
        MARKER_START,
        "# GoATee AI Delivery & Budget Governance",
        "",
        "This repository is governed by GoATee (Spec and Delivery Control Layer).",
        "All AI coding assistants and developers must adhere to the following delivery constraints:",
        "",
        "## General Agent Invariants",
        "1. Never edit files outside the approved Build Budget ('Expected Surface').",
        "2. Never touch or mutate protected files in 'Blast Radius'.",
        "3. Never tamper with or edit '.goatee/changes/**/change.md' post-approval; contracts are cryptographically hashed with Contract-SHA256.",
        "4. If additional files genuinely need to be modified, request human approval via 'goatee amend <change-id> --add-surface <path> --note <rationale>'.",
        "5. Pre-flight check: Run 'goatee check' before committing code to verify your working tree against the approved budget.",
        "6. Verification: When finished, run 'goatee verify' to generate deterministic machine delivery receipts.",
        ""
    ]

    if change_id and contract:
        lines.append(f"## Active Change: {change_id}")
        if contract.intent:
            lines.append(f"**Intent**: {contract.intent}")
        lines.append("")
        lines.append("### Approved Build Budget")
        lines.append("Expected Surface (ONLY these paths may be modified):")
        for s in contract.build_budget.expected_surface:
            lines.append(f"- {s}")
        if not contract.build_budget.expected_surface:
            lines.append("- (none declared)")
        lines.append("")
        lines.append("Blast Radius (Strictly protected - DO NOT TOUCH):")
        for b in contract.build_budget.blast_radius:
            lines.append(f"- {b}")
        if not contract.build_budget.blast_radius:
            lines.append("- (none declared)")
        lines.append("")
        lines.append(f"Dependency Policy: {contract.build_budget.dependency_policy or 'No new runtime dependencies'}")
        lines.append("")
    else:
        lines.append("## Active Change")
        lines.append("No active change currently locked. Run 'goatee status' or scaffold a new change with 'goatee new <id>'.")
        lines.append("")

    lines.append(MARKER_END)
    return "\n".join(lines)


def export_agent_rules(
    root: Path,
    target: str = "all",
    custom_output: Optional[Path] = None,
    active_change_id: Optional[str] = None
) -> List[Tuple[Path, bool]]:
    """Export or update GoATee rule blocks into targeted agent rule files.
    
    Returns list of (file_path, was_created_or_updated) tuples.
    """
    block = generate_rules_block(root, active_change_id)
    results: List[Tuple[Path, bool]] = []

    target_paths: List[Path] = []
    if custom_output:
        target_paths.append(custom_output if custom_output.is_absolute() else root / custom_output)
    elif target.lower() == "all":
        for rel in SUPPORTED_TARGETS.values():
            target_paths.append(root / rel)
    else:
        rel = SUPPORTED_TARGETS.get(target.lower())
        if not rel:
            raise ValueError(f"Unknown target '{target}'. Supported targets: {', '.join(list(SUPPORTED_TARGETS.keys()) + ['all'])}")
        target_paths.append(root / rel)

    for path in target_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if MARKER_START in content and MARKER_END in content:
                start_idx = content.find(MARKER_START)
                end_idx = content.find(MARKER_END) + len(MARKER_END)
                new_content = content[:start_idx] + block + content[end_idx:]
            else:
                new_content = content.rstrip() + "\n\n" + block + "\n"
        else:
            new_content = block + "\n"

        path.write_text(new_content, encoding="utf-8")
        results.append((path, True))

    return results
