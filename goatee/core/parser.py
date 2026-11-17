"""Narrow key-value metadata and structured Markdown parser using standard library only."""

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Union
from goatee.core.models import AcceptanceCriterion, BuildBudget, ChangeContract, ContractState


def parse_metadata_block(text: str) -> Dict[str, str]:
    """Parse Key: Value pairs from text using standard library string splitting.
    
    Lines starting with # are comments. Empty lines are ignored.
    Splits only on the first colon.
    """
    metadata: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            metadata[key.strip()] = val.strip()
    return metadata


class ContractSyntaxError(Exception):
    """Raised when a change contract violates required structural syntax."""
    pass


def parse_change_contract(path: Path) -> ChangeContract:
    """Parse a change.md file into a ChangeContract object.

    Raises ContractSyntaxError if required sections or metadata keys are missing.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Contract not found: {path}")

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Extract change_id from header (e.g. "# Change: 001-export-csv")
    change_id: Optional[str] = None
    for line in lines:
        if line.startswith("# Change:"):
            val = line.split(":", 1)[1].strip()
            if val:
                change_id = val
            break

    if not change_id:
        raise ContractSyntaxError("Missing or empty '# Change: <change-id>' header.")

    if path.parent.name != change_id:
        raise ContractSyntaxError(
            f"Contract change-id '{change_id}' does not match directory name '{path.parent.name}'."
        )

    # Required sections check
    required_sections = [
        "## Intent",
        "## Agreed Scope",
        "### In Scope",
        "### Out of Scope",
        "## Acceptance Criteria",
        "## Build Budget",
        "### Expected Surface",
        "### Reuse Strategy",
        "### Blast Radius",
        "## Contract State",
    ]
    for sec in required_sections:
        if sec not in content:
            raise ContractSyntaxError(f"Missing required section: '{sec}'.")

    # Extract Contract State
    contract_state = ContractState()
    state_idx = content.find("## Contract State")
    state_block = content[state_idx:]
    state_meta = parse_metadata_block(state_block)

    if "Agreement-Status" not in state_meta:
        raise ContractSyntaxError("Missing required field: 'Agreement-Status' in '## Contract State'.")
    agreement_status = state_meta["Agreement-Status"]
    if agreement_status not in ("DRAFT", "APPROVED"):
        raise ContractSyntaxError(
            f"Invalid Agreement-Status '{agreement_status}'. Must be 'DRAFT' or 'APPROVED'."
        )
    contract_state.agreement_status = agreement_status

    if "Budget-Status" not in state_meta:
        raise ContractSyntaxError("Missing required field: 'Budget-Status' in '## Contract State'.")
    budget_status = state_meta["Budget-Status"]
    if budget_status not in ("DRAFT", "APPROVED"):
        raise ContractSyntaxError(
            f"Invalid Budget-Status '{budget_status}'. Must be 'DRAFT' or 'APPROVED'."
        )
    contract_state.budget_status = budget_status

    if "Baseline-Commit" not in state_meta:
        raise ContractSyntaxError("Missing required field: 'Baseline-Commit' in '## Contract State'.")
    baseline = state_meta["Baseline-Commit"].strip()
    contract_state.baseline_commit = baseline if baseline else None

    # Extract Acceptance Criteria
    criteria: List[AcceptanceCriterion] = []
    ac_matches = re.finditer(
        r"-\s+(AC-\d+):\s*(.*?)(?=\n-\s+AC-|\n##|\Z)",
        content,
        re.DOTALL
    )
    for m in ac_matches:
        ac_id = m.group(1).strip()
        body = m.group(2).strip()
        body_lines = body.splitlines()
        desc = body_lines[0].strip() if body_lines else ""
        
        meta = parse_metadata_block(body)
        ev_type = meta.get("type", "automated").lower()
        command = meta.get("command")
        expected = meta.get("expected")
        instruction = meta.get("instruction")
        
        criteria.append(AcceptanceCriterion(
            id=ac_id,
            description=desc,
            evidence_type=ev_type,
            command=command,
            expected=expected,
            instruction=instruction,
        ))

    if not criteria:
        raise ContractSyntaxError("Contract must declare at least one Acceptance Criterion.")

    # Extract Build Budget
    budget = BuildBudget()
    budget_idx = content.find("## Build Budget")
    budget_block = content[budget_idx:state_idx]

    if "Dependency-Policy:" not in budget_block:
        raise ContractSyntaxError("Missing required field: 'Dependency-Policy:' in '## Build Budget'.")

    def extract_bullet_list(section_name: str, text: str) -> List[str]:
        pattern = rf"###\s+{re.escape(section_name)}\s*\n(.*?)(?=\n###|\n[A-Za-z0-9_-]+:|\Z)"
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return []
        items = []
        for b_line in match.group(1).splitlines():
            b_line = b_line.strip()
            if b_line.startswith("- "):
                items.append(b_line[2:].strip())
        return items

    budget.expected_surface = extract_bullet_list("Expected Surface", budget_block)
    budget.reuse_strategy = extract_bullet_list("Reuse Strategy", budget_block)
    budget.blast_radius = extract_bullet_list("Blast Radius", budget_block)
    budget_meta = parse_metadata_block(budget_block)
    budget.dependency_policy = budget_meta.get("Dependency-Policy", "")

    return ChangeContract(
        change_id=change_id,
        acceptance_criteria=criteria,
        build_budget=budget,
        contract_state=contract_state,
    )


def update_contract_state(path: Path, key: str, value: str) -> None:
    """Safely updates or appends a key-value pair in ## Contract State of a change.md file."""
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    
    in_state_section = False
    updated = False
    new_lines = []

    for line in lines:
        if line.strip() == "## Contract State":
            in_state_section = True
            new_lines.append(line)
            continue
        
        if in_state_section:
            if line.startswith("## "):
                in_state_section = False
            elif line.startswith(f"{key}:"):
                new_lines.append(f"{key}: {value}")
                updated = True
                continue

        new_lines.append(line)

    if in_state_section and not updated:
        new_lines.append(f"{key}: {value}")
        updated = True

    if not updated:
        # Section didn't exist or key was never written
        if not in_state_section:
            new_lines.append("")
            new_lines.append("## Contract State")
            new_lines.append(f"{key}: {value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def compute_contract_hash(content: Union[str, Path]) -> str:
    """Compute deterministic SHA-256 hash over canonical contract agreement content.

    Excludes mutable state fields (Baseline-Commit, Contract-SHA256).
    Normalizes line-level whitespace to prevent CRLF or indentation jitter.
    Accepts either raw contract text string or Path to contract file.
    """
    if isinstance(content, Path):
        content = content.read_text(encoding="utf-8")
    lines = []
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("Baseline-Commit:") or trimmed.startswith("Contract-SHA256:"):
            continue
        lines.append(trimmed)
    canonical = "\n".join(lines).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_contract_hash(path: Path) -> Optional[str]:
    """Read Contract-SHA256 from ## Contract State if present in change.md."""
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    state_idx = content.find("## Contract State")
    if state_idx == -1:
        return None
    meta = parse_metadata_block(content[state_idx:])
    val = meta.get("Contract-SHA256", "").strip()
    return val if val else None
