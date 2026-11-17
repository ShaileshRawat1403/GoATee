"""Pure standard library Model Context Protocol (MCP) stdio server for GoATee."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

from goatee.core.budget import compare_budget, get_working_tree_changed_files, GitInspectionError
from goatee.core.parser import parse_change_contract, ContractSyntaxError
from goatee.core.status import (
    find_project_root,
    get_project_status,
    format_project_overview,
    get_change_report,
    format_change_detail,
    get_working_tree_state,
    resolve_current_head,
    resolve_active_change_id,
    ControlStateError,
    ReceiptSyntaxError,
)

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "goatee"
SERVER_VERSION = "0.3.0"

TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "goatee_get_contract",
        "description": "Retrieve the governing change contract (intent, in/out scope, acceptance criteria, build budget, and contract state) for a specified or active change.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "Identifier of the change (e.g. '008-preflight-and-agent-bridge'). If omitted, automatically resolves active change from branch or workspace."
                }
            }
        }
    },
    {
        "name": "goatee_check_budget",
        "description": "Evaluate current uncommitted working-tree modifications (staged and unstaged) against the approved Build Budget (expected surface and blast radius). Zero side effects.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "Identifier of the change. If omitted, automatically resolves active change from branch or workspace."
                }
            }
        }
    },
    {
        "name": "goatee_get_status",
        "description": "Inspect GoATee project status overview or detailed change report without mutating repository state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "Optional change identifier. If omitted, returns project-wide overview."
                }
            }
        }
    },
    {
        "name": "goatee_propose_budget",
        "description": "Propose or update the technical Build Budget (expected surface, reuse strategy, dependency policy, and blast radius) for a change contract in DRAFT status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "Identifier of the change. If omitted, automatically resolves active change."
                },
                "expected_surface": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files or directories expected to be modified or created."
                },
                "reuse_strategy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of existing components or patterns to reuse."
                },
                "dependency_policy": {
                    "type": "string",
                    "description": "Dependency policy statement (e.g. 'No new runtime dependencies')."
                },
                "blast_radius": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of sensitive/protected paths that must remain untouched."
                }
            },
            "required": ["expected_surface"]
        }
    }
]


def handle_get_contract(args: Dict[str, Any], root: Path) -> Dict[str, Any]:
    change_id = args.get("change_id") or resolve_active_change_id(root)
    if not change_id:
        return {
            "content": [{"type": "text", "text": "Error: No change-id provided and active change could not be uniquely inferred."}],
            "isError": True
        }

    contract_path = root / ".goatee" / "changes" / change_id / "change.md"
    if not contract_path.is_file():
        return {
            "content": [{"type": "text", "text": f"Error: Change contract not found for '{change_id}' at {contract_path}."}],
            "isError": True
        }

    try:
        contract = parse_change_contract(contract_path)
    except ContractSyntaxError as cse:
        return {
            "content": [{"type": "text", "text": f"Contract syntax error in '{change_id}': {cse}"}],
            "isError": True
        }

    # Format structured contract representation
    result = {
        "change_id": contract.change_id,
        "agreement_status": contract.contract_state.agreement_status,
        "budget_status": contract.contract_state.budget_status,
        "baseline_commit": contract.contract_state.baseline_commit,
        "intent": contract.intent,
        "scope_in": contract.agreed_scope_in,
        "scope_out": contract.agreed_scope_out,
        "acceptance_criteria": [
            {
                "id": ac.id,
                "description": ac.description,
                "type": ac.evidence_type,
                "command": ac.command,
                "expected": ac.expected,
                "instruction": ac.instruction
            }
            for ac in contract.acceptance_criteria
        ],
        "build_budget": {
            "expected_surface": contract.build_budget.expected_surface if contract.build_budget else [],
            "reuse_strategy": contract.build_budget.reuse_strategy if contract.build_budget else [],
            "dependency_policy": contract.build_budget.dependency_policy if contract.build_budget else "",
            "blast_radius": contract.build_budget.blast_radius if contract.build_budget else []
        }
    }

    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False
    }


def handle_check_budget(args: Dict[str, Any], root: Path) -> Dict[str, Any]:
    change_id = args.get("change_id") or resolve_active_change_id(root)
    if not change_id:
        return {
            "content": [{"type": "text", "text": "Error: No change-id provided and active change could not be uniquely inferred."}],
            "isError": True
        }

    change_file = root / ".goatee" / "changes" / change_id / "change.md"
    if not change_file.is_file():
        return {
            "content": [{"type": "text", "text": f"Error: Change contract not found at {change_file}."}],
            "isError": True
        }

    try:
        contract = parse_change_contract(change_file)
    except ContractSyntaxError as cse:
        return {
            "content": [{"type": "text", "text": f"Contract syntax error in '{change_id}': {cse}"}],
            "isError": True
        }

    baseline = contract.contract_state.baseline_commit
    if not baseline:
        return {
            "content": [{"type": "text", "text": f"Error: Change '{change_id}' has no Baseline-Commit locked. Run 'goatee approve {change_id}' before checking budget."}],
            "isError": True
        }

    if not contract.build_budget:
        return {
            "content": [{"type": "text", "text": f"Error: Change '{change_id}' has no Build Budget declared."}],
            "isError": True
        }

    try:
        changed_files = get_working_tree_changed_files(baseline, cwd=root)
        comparison = compare_budget(changed_files, contract.build_budget)
    except GitInspectionError as gie:
        return {
            "content": [{"type": "text", "text": f"Git inspection error: {gie}"}],
            "isError": True
        }

    passed = not comparison.surface_deviations and not comparison.protected_changes
    report = {
        "change_id": change_id,
        "baseline_commit": baseline,
        "passed": passed,
        "expected_changes": comparison.expected_changes,
        "surface_deviations": comparison.surface_deviations,
        "protected_changes": comparison.protected_changes,
        "ignored_control_files": comparison.ignored_control_files,
    }

    summary_text = (
        f"Pre-Flight Budget Check: {'PASSED' if passed else 'FAILED'}\n"
        f"Expected Changes ({len(comparison.expected_changes)}):\n" +
        ("\n".join(f"  - {f}" for f in comparison.expected_changes) or "  (none)") + "\n"
        f"Surface Deviations ({len(comparison.surface_deviations)}):\n" +
        ("\n".join(f"  - {f}" for f in comparison.surface_deviations) or "  (none)") + "\n"
        f"Protected Changes ({len(comparison.protected_changes)}):\n" +
        ("\n".join(f"  - {f}" for f in comparison.protected_changes) or "  (none)")
    )

    return {
        "content": [{"type": "text", "text": summary_text}],
        "isError": not passed
    }


def handle_get_status(args: Dict[str, Any], root: Path) -> Dict[str, Any]:
    change_id = args.get("change_id")

    if change_id:
        try:
            working_tree = get_working_tree_state(root)
            current_head = resolve_current_head(root)
            change_report = get_change_report(root, change_id, working_tree, current_head)
            contract_path = root / ".goatee" / "changes" / change_id / "change.md"
            contract = parse_change_contract(contract_path) if contract_path.is_file() else None
            output = format_change_detail(change_report, contract)
            return {
                "content": [{"type": "text", "text": output}],
                "isError": False
            }
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True
            }
    else:
        try:
            project_report = get_project_status(root)
            output = format_project_overview(project_report)
            return {
                "content": [{"type": "text", "text": output}],
                "isError": False
            }
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True
            }


def handle_propose_budget(args: Dict[str, Any], root: Path) -> Dict[str, Any]:
    change_id = args.get("change_id") or resolve_active_change_id(root)
    if not change_id:
        return {
            "content": [{"type": "text", "text": "Error: No change-id provided and active change could not be uniquely inferred."}],
            "isError": True
        }

    contract_path = root / ".goatee" / "changes" / change_id / "change.md"
    if not contract_path.is_file():
        return {
            "content": [{"type": "text", "text": f"Error: Change contract not found for '{change_id}' at {contract_path}."}],
            "isError": True
        }

    try:
        contract = parse_change_contract(contract_path)
    except ContractSyntaxError as cse:
        return {
            "content": [{"type": "text", "text": f"Contract syntax error in '{change_id}': {cse}"}],
            "isError": True
        }

    if contract.contract_state.budget_status == "APPROVED" and contract.contract_state.baseline_commit:
        return {
            "content": [{"type": "text", "text": f"Error: Change '{change_id}' already has an APPROVED budget and locked baseline commit. Use 'goatee amend' for post-approval amendments."}],
            "isError": True
        }

    expected_surface = args.get("expected_surface", [])
    reuse_strategy = args.get("reuse_strategy", [])
    dependency_policy = args.get("dependency_policy", "No new runtime dependencies")
    blast_radius = args.get("blast_radius", [])

    content = contract_path.read_text(encoding="utf-8")
    budget_idx = content.find("## Build Budget")
    state_idx = content.find("## Contract State")
    if budget_idx == -1 or state_idx == -1:
        return {
            "content": [{"type": "text", "text": "Error: Malformed contract: missing ## Build Budget or ## Contract State section."}],
            "isError": True
        }

    exp_str = "\n".join(f"- {s}" for s in expected_surface) or "- (none)"
    reuse_str = "\n".join(f"- {r}" for r in reuse_strategy) or "- (none)"
    blast_str = "\n".join(f"- {b}" for b in blast_radius) or "- (none)"

    new_budget_section = (
        "## Build Budget\n\n"
        "### Expected Surface\n"
        f"{exp_str}\n\n"
        "### Reuse Strategy\n"
        f"{reuse_str}\n\n"
        f"Dependency-Policy: {dependency_policy}\n\n"
        "### Blast Radius\n"
        f"{blast_str}\n\n"
    )

    new_content = content[:budget_idx] + new_budget_section + content[state_idx:]
    contract_path.write_text(new_content, encoding="utf-8")

    return {
        "content": [{
            "type": "text",
            "text": (
                f"Proposed Build Budget updated for '{change_id}':\n"
                f"- Expected Surface: {len(expected_surface)} path(s)\n"
                f"- Blast Radius: {len(blast_radius)} path(s)\n"
                f"- Dependency Policy: {dependency_policy}\n"
                "Next step: Human must inspect and approve the budget before implementation."
            )
        }],
        "isError": False
    }


def process_mcp_message(message: Dict[str, Any], root: Path) -> Optional[Dict[str, Any]]:
    """Process a single JSON-RPC 2.0 message according to MCP specification."""
    req_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})

    # 1. Initialize
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION
                }
            }
        }

    # 2. Notification initialized
    if method == "notifications/initialized":
        return None

    # 3. Ping
    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        }

    # 4. Tools list
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS_DEFINITIONS
            }
        }

    # 5. Tools call
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "goatee_get_contract":
            result = handle_get_contract(arguments, root)
        elif tool_name == "goatee_check_budget":
            result = handle_check_budget(arguments, root)
        elif tool_name == "goatee_get_status":
            result = handle_get_status(arguments, root)
        elif tool_name == "goatee_propose_budget":
            result = handle_propose_budget(arguments, root)
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: '{tool_name}'"
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }

    # Unknown method
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: '{method}'"
            }
        }

    return None


def run_mcp_server(
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    repo_root: Optional[Path] = None
) -> int:
    """Run MCP JSON-RPC server on stdio until EOF."""
    root = repo_root or find_project_root()

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as jde:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {jde}"
                }
            }
            stdout.write(json.dumps(err_resp) + "\n")
            stdout.flush()
            continue

        resp = process_mcp_message(req, root)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()

    return 0
