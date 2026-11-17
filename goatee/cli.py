"""Command line interface for GoATee v0.1 Phase 1."""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from goatee.core.parser import parse_change_contract, update_contract_state, ContractSyntaxError
from goatee.core.budget import GitInspectionError


SAFE_CHANGE_ID_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def validate_change_id(change_id: str) -> None:
    """Validate change-id against safe restricted format to prevent path traversal."""
    if not change_id or not SAFE_CHANGE_ID_REGEX.match(change_id):
        raise ValueError(
            f"Invalid change-id '{change_id}'. "
            "Must start with an alphanumeric character, contain only letters, numbers, hyphens, or underscores, "
            "and be between 1 and 64 characters long with no path separators."
        )


def resolve_or_fail_change_id(raw_change_id: Optional[str]) -> str:
    """Resolve change ID from argument or active repository context, validating format."""
    from goatee.core.status import resolve_active_change_id
    change_id = raw_change_id or resolve_active_change_id(Path.cwd())
    if not change_id:
        raise ValueError(
            "No change-id specified and active change could not be uniquely inferred from Git branch or workspace. Specify <change-id>."
        )
    validate_change_id(change_id)
    return change_id


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize .goatee/project.md in current directory."""
    cwd = Path.cwd()
    goatee_dir = cwd / ".goatee"
    project_file = goatee_dir / "project.md"

    if project_file.exists():
        print(f"Error: GoATee project already initialized at {project_file}", file=sys.stderr)
        return 1

    goatee_dir.mkdir(parents=True, exist_ok=True)
    changes_dir = goatee_dir / "changes"
    changes_dir.mkdir(exist_ok=True)

    template_path = Path(__file__).parent / "templates" / "project.md"
    content = template_path.read_text(encoding="utf-8")
    content = content.replace("{project_name}", cwd.name)

    project_file.write_text(content, encoding="utf-8")
    print(f"Initialized GoATee project at {project_file}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Scaffold .goatee/changes/<change-id>/change.md."""
    try:
        validate_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_dir = cwd / ".goatee" / "changes" / args.change_id
    change_file = change_dir / "change.md"

    if change_dir.exists():
        print(f"Error: Change '{args.change_id}' already exists at {change_dir}", file=sys.stderr)
        return 1

    change_dir.mkdir(parents=True, exist_ok=True)
    template_path = Path(__file__).parent / "templates" / "change.md"
    content = template_path.read_text(encoding="utf-8")
    content = content.replace("{change_id}", args.change_id)

    change_file.write_text(content, encoding="utf-8")
    print(f"Created change contract at {change_file}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Validate approved status, check clean repo state, and record baseline commit."""
    try:
        change_id = resolve_or_fail_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_file = cwd / ".goatee" / "changes" / change_id / "change.md"

    if not change_file.is_file():
        print(f"Error: Change contract not found at {change_file}", file=sys.stderr)
        return 1

    # 1. Check Git repository
    res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print("Error: Not inside a valid Git repository.", file=sys.stderr)
        return 1

    # 2. Check HEAD commit exists
    head_res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=cwd)
    if head_res.returncode != 0:
        print("Error: Git HEAD does not exist (repository has no commits).", file=sys.stderr)
        return 1
    baseline_sha = head_res.stdout.strip()

    # 3. Check for uncommitted application-code changes outside .goatee/
    status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=cwd)
    if status_res.returncode != 0:
        print("Error: Failed to inspect git status.", file=sys.stderr)
        return 1

    uncommitted_app_changes = []
    for line in status_res.stdout.splitlines():
        if not line.strip():
            continue
        # Format is 'XY path' or 'XY orig -> path'
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ")[1].strip()
        if not path_part.startswith(".goatee/"):
            uncommitted_app_changes.append(line)

    if uncommitted_app_changes:
        print(
            "Error: Working tree contains uncommitted application changes outside .goatee/:\n" +
            "\n".join(f"  {c}" for c in uncommitted_app_changes) +
            "\nCommit or stash application changes before recording baseline.",
            file=sys.stderr
        )
        return 1

    # 4. Parse contract and verify Agreement & Budget statuses
    try:
        contract = parse_change_contract(change_file)
    except ContractSyntaxError as cse:
        print(f"Contract syntax error:\n{cse}", file=sys.stderr)
        return 1
    if contract.contract_state.baseline_commit:
        print(
            f"Error: Change '{change_id}' already has a baseline commit: "
            f"{contract.contract_state.baseline_commit}",
            file=sys.stderr
        )
        return 1

    if contract.contract_state.agreement_status != "APPROVED":
        print(
            f"Error: Agreement-Status is '{contract.contract_state.agreement_status}', must be 'APPROVED' before approval.",
            file=sys.stderr
        )
        return 1

    if contract.contract_state.budget_status != "APPROVED":
        print(
            f"Error: Budget-Status is '{contract.contract_state.budget_status}', must be 'APPROVED' before approval.",
            file=sys.stderr
        )
        return 1

    # 5. Record baseline commit in change.md
    update_contract_state(change_file, "Baseline-Commit", baseline_sha)
    print(f"Approved change '{change_id}' and locked baseline commit: {baseline_sha}")
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    """Render portable, model-agnostic handoff contract to stdout."""
    try:
        change_id = resolve_or_fail_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_file = cwd / ".goatee" / "changes" / change_id / "change.md"

    if not change_file.is_file():
        print(f"Error: Change contract not found at {change_file}", file=sys.stderr)
        return 1

    try:
        contract = parse_change_contract(change_file)
    except ContractSyntaxError as cse:
        print(f"Contract syntax error:\n{cse}", file=sys.stderr)
        return 1

    if contract.contract_state.agreement_status != "APPROVED":
        print(
            f"Error: Agreement-Status is '{contract.contract_state.agreement_status}', "
            "must be 'APPROVED' before handoff.",
            file=sys.stderr
        )
        return 1

    if contract.contract_state.budget_status != "APPROVED":
        print(
            f"Error: Budget-Status is '{contract.contract_state.budget_status}', "
            "must be 'APPROVED' before handoff.",
            file=sys.stderr
        )
        return 1

    if not contract.contract_state.baseline_commit:
        print(
            "Error: Change has no Baseline-Commit recorded. "
            f"Run 'goatee approve {change_id}' before handoff.",
            file=sys.stderr
        )
        return 1

    # Extract Intent, Scope, and Project Constraints directly from the files
    content = change_file.read_text(encoding="utf-8")

    def extract_section(section_header: str, end_headers: list[str], text: str) -> str:
        idx = text.find(section_header)
        if idx == -1:
            return ""
        sub = text[idx + len(section_header):]
        closest_end = len(sub)
        for eh in end_headers:
            e_idx = sub.find(eh)
            if e_idx != -1 and e_idx < closest_end:
                closest_end = e_idx
        return sub[:closest_end].strip()

    intent_text = extract_section("## Intent", ["## Agreed Scope", "## Acceptance Criteria"], content)
    scope_in_text = extract_section("### In Scope", ["### Out of Scope", "## Acceptance Criteria"], content)
    scope_out_text = extract_section("### Out of Scope", ["## Acceptance Criteria", "## Build Budget"], content)

    # Format Acceptance Criteria
    ac_lines = []
    for ac in contract.acceptance_criteria:
        ac_lines.append(f"- {ac.id}: {ac.description}")
        ac_lines.append("  Evidence:")
        ac_lines.append(f"    type: {ac.evidence_type}")
        if ac.evidence_type == "automated":
            if ac.command:
                ac_lines.append(f"    command: {ac.command}")
            if ac.expected:
                ac_lines.append(f"    expected: {ac.expected}")
        elif ac.evidence_type == "manual":
            if ac.instruction:
                ac_lines.append(f"    instruction: {ac.instruction}")
        ac_lines.append("")
    ac_formatted = "\n".join(ac_lines).strip()

    # Format Build Budget
    budget = contract.build_budget
    expected_surf = "\n".join(f"- {s}" for s in (budget.expected_surface if budget else [])) or "- (none declared)"
    reuse_strat = "\n".join(f"- {r}" for r in (budget.reuse_strategy if budget else [])) or "- (none declared)"
    dep_policy = (budget.dependency_policy if budget and budget.dependency_policy else "No new runtime dependencies")
    blast_rad = "\n".join(f"- {b}" for b in (budget.blast_radius if budget else [])) or "- (none declared)"

    # Read project constraints if project.md exists
    project_file = cwd / ".goatee" / "project.md"
    project_constraints = ""
    if project_file.is_file():
        proj_content = project_file.read_text(encoding="utf-8")
        project_constraints = extract_section("## Permanent Constraints", ["## "], proj_content)
    if not project_constraints:
        project_constraints = "- Standard library only at runtime; no external runtime dependencies."

    # Load handoff template
    template_path = Path(__file__).parent / "templates" / "handoff.md"
    template_text = template_path.read_text(encoding="utf-8")

    handoff_rendered = template_text.format(
        change_id=contract.change_id,
        baseline_commit=contract.contract_state.baseline_commit,
        intent=intent_text or "(none declared)",
        scope_in=scope_in_text or "- (none declared)",
        scope_out=scope_out_text or "- (none declared)",
        acceptance_criteria=ac_formatted or "(none declared)",
        expected_surface=expected_surf,
        reuse_strategy=reuse_strat,
        dependency_policy=dep_policy,
        blast_radius=blast_rad,
        project_constraints=project_constraints,
    )

    sys.stdout.write(handoff_rendered + "\n")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Execute deterministic verification and compile delivery receipt.md."""
    try:
        change_id = resolve_or_fail_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_dir = cwd / ".goatee" / "changes" / change_id
    change_file = change_dir / "change.md"

    if not change_file.is_file():
        print(f"Error: Change contract not found at {change_file}", file=sys.stderr)
        return 1

    # 1. Pre-Check: Check valid Git repository
    res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print("Error: Not inside a valid Git repository.", file=sys.stderr)
        return 1

    # Check candidate HEAD commit exists (initial HEAD resolution fails closed)
    head_res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=cwd)
    if head_res.returncode != 0 or not head_res.stdout.strip():
        print(f"GIT INSPECTION FAILED:\nUnable to resolve HEAD: {head_res.stderr.strip()}", file=sys.stderr)
        return 1

    # 2. Pre-Check: Require completely clean Git-visible working tree (including current change.md)
    status_res = subprocess.run(["git", "status", "--porcelain=v1"], capture_output=True, text=True, cwd=cwd)
    if status_res.returncode != 0:
        print(f"GIT INSPECTION FAILED:\ngit status failed: {status_res.stderr.strip()}", file=sys.stderr)
        return 1

    uncommitted = [line for line in status_res.stdout.splitlines() if line.strip()]
    if uncommitted:
        print(
            "VERIFICATION BLOCKED: Working tree must be clean. "
            "Commit the delivered implementation and governing change contract before verification.\n"
            "Uncommitted changes:\n" +
            "\n".join(f"  {c}" for c in uncommitted),
            file=sys.stderr
        )
        return 1

    # 3. Pre-Check: Contract must be approved and have a baseline commit
    try:
        contract = parse_change_contract(change_file)
    except ContractSyntaxError as cse:
        print(f"Contract syntax error:\n{cse}", file=sys.stderr)
        return 1

    if contract.contract_state.agreement_status != "APPROVED":
        print(
            f"Error: Agreement-Status is '{contract.contract_state.agreement_status}', "
            "must be 'APPROVED' before verification.",
            file=sys.stderr
        )
        return 1

    if contract.contract_state.budget_status != "APPROVED":
        print(
            f"Error: Budget-Status is '{contract.contract_state.budget_status}', "
            "must be 'APPROVED' before verification.",
            file=sys.stderr
        )
        return 1

    if not contract.contract_state.baseline_commit:
        print(
            "Error: Change has no Baseline-Commit recorded. "
            f"Run 'goatee approve {change_id}' before verification.",
            file=sys.stderr
        )
        return 1

    # 4. Run Verification Engine
    from goatee.core.verifier import verify_change, render_receipt_markdown
    try:
        result = verify_change(contract, cwd)
    except GitInspectionError as gie:
        print(f"GIT INSPECTION FAILED:\n{gie}", file=sys.stderr)
        return 1
    except ValueError as ve:
        print(f"Error in evidence declarations: {ve}", file=sys.stderr)
        return 1

    # 5. Write receipt.md
    template_path = Path(__file__).parent / "templates" / "receipt.md"
    receipt_content = render_receipt_markdown(result, template_path)
    receipt_file = change_dir / "receipt.md"
    receipt_file.write_text(receipt_content, encoding="utf-8")

    print(f"Verification complete for '{change_id}'.")
    print(f"Verdict: {result.verdict}")
    print(f"Delivery receipt compiled at {receipt_file}")

    return 0 if result.verdict == "PASS" else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Display GoATee project overview or detailed change status."""
    from goatee.core.status import (
        find_project_root,
        get_project_status,
        format_project_overview,
        get_change_report,
        format_change_detail,
        get_working_tree_state,
        resolve_current_head,
        ControlStateError,
        ReceiptSyntaxError,
    )

    try:
        root = find_project_root()
    except ControlStateError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.change_id:
        try:
            validate_change_id(args.change_id)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        try:
            working_tree = get_working_tree_state(root)
            current_head = resolve_current_head(root)
            change_report = get_change_report(root, args.change_id, working_tree, current_head)
            contract_path = root / ".goatee" / "changes" / args.change_id / "change.md"
            contract = parse_change_contract(contract_path) if contract_path.is_file() else None
            output = format_change_detail(change_report, contract)
            print(output)
            return 0
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        try:
            project_report = get_project_status(root)
            output = format_project_overview(project_report)
            print(output)
            return 0
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1


def cmd_check(args: argparse.Namespace) -> int:
    """Pre-flight budget inspection of working tree changes against approved expected surface and blast radius."""
    try:
        change_id = resolve_or_fail_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_file = cwd / ".goatee" / "changes" / change_id / "change.md"

    if not change_file.is_file():
        print(f"Error: Change contract not found at {change_file}", file=sys.stderr)
        return 1

    # 1. Check Git repository
    res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print("Error: Not inside a valid Git repository.", file=sys.stderr)
        return 1

    # 2. Parse contract
    try:
        contract = parse_change_contract(change_file)
    except ContractSyntaxError as cse:
        print(f"Contract syntax error:\n{cse}", file=sys.stderr)
        return 1

    baseline = contract.contract_state.baseline_commit
    if not baseline:
        print(
            f"Error: Change '{change_id}' has no Baseline-Commit locked. "
            f"Run 'goatee approve {change_id}' before checking budget.",
            file=sys.stderr
        )
        return 1

    if not contract.build_budget:
        print(f"Error: Change '{change_id}' has no Build Budget declared.", file=sys.stderr)
        return 1

    # 3. Compare working tree against budget
    from goatee.core.budget import get_working_tree_changed_files, compare_budget, GitInspectionError
    try:
        changed_files = get_working_tree_changed_files(baseline, cwd=cwd)
        comparison = compare_budget(changed_files, contract.build_budget)
    except GitInspectionError as gie:
        print(f"GIT INSPECTION FAILED:\n{gie}", file=sys.stderr)
        return 1

    total_changes = len(comparison.expected_changes) + len(comparison.surface_deviations) + len(comparison.protected_changes)
    print(f"# Pre-Flight Budget Check: {change_id}")
    print(f"Baseline: {baseline}")
    print(f"Working Tree Changes: {total_changes}")
    print()

    print(f"Expected Changes: {len(comparison.expected_changes)}")
    if comparison.expected_changes:
        for f in comparison.expected_changes:
            print(f"- {f}")
    else:
        print("- (none)")
    print()

    print("Surface Deviations:")
    if comparison.surface_deviations:
        for f in comparison.surface_deviations:
            print(f"- {f}")
    else:
        print("- (none)")
    print()

    print("Protected Changes:")
    if comparison.protected_changes:
        for f in comparison.protected_changes:
            print(f"- {f}")
    else:
        print("- (none)")
    print()

    if comparison.ignored_control_files:
        print(f"Ignored Control Files: {len(comparison.ignored_control_files)}")
        for f in comparison.ignored_control_files:
            print(f"- {f}")
        print()

    if comparison.surface_deviations or comparison.protected_changes:
        print("PRE-FLIGHT CHECK FAILED: Working tree contains unapproved deviations or touches protected changes.", file=sys.stderr)
        return 1

    print("PRE-FLIGHT CHECK PASSED: Working tree is within approved Build Budget.")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Launch pure standard-library Model Context Protocol (MCP) server over stdio."""
    from goatee.core.mcp import run_mcp_server
    return run_mcp_server(repo_root=Path.cwd())


def cmd_attest(args: argparse.Namespace) -> int:
    """Record human attestation into review.md."""
    try:
        change_id = resolve_or_fail_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    from goatee.core.status import (
        ControlStateError,
        ReceiptSyntaxError,
    )
    from goatee.core.parser import ContractSyntaxError

    cwd = Path.cwd()

    if getattr(args, "interactive", False):
        from goatee.core.attest import run_interactive_attestation
        try:
            return run_interactive_attestation(cwd, change_id)
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Non-interactive mode requires --note and either --verified or --rejected
    if not args.note:
        print("Error: --note is required in non-interactive mode.", file=sys.stderr)
        return 1

    if not args.verified and not args.rejected:
        print("Error: Exactly one of --verified or --rejected must be specified in non-interactive mode.", file=sys.stderr)
        return 1

    if args.verified and not args.criterion:
        print("Error: --verified requires --criterion.", file=sys.stderr)
        return 1

    try:
        from goatee.core.attest import record_attestation
        record_attestation(
            repo_root=cwd,
            change_id=change_id,
            criterion_id=args.criterion,
            verified=args.verified,
            rejected=args.rejected,
            note=args.note,
        )
        action_str = "VERIFIED" if args.verified else "REJECTED"
        target_str = f"criterion '{args.criterion}'" if args.criterion else f"change '{change_id}'"
        print(f"Recorded {action_str} attestation for {target_str} in .goatee/changes/{change_id}/review.md")
        return 0
    except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


class GoateeArgumentParser(argparse.ArgumentParser):
    """Custom argument parser enforcing conditional sub-command validation."""
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args=args, namespace=namespace)
        if getattr(parsed, "command", None) == "attest":
            if getattr(parsed, "interactive", False):
                if getattr(parsed, "verified", False) or getattr(parsed, "rejected", False):
                    self.error("cannot combine --interactive with --verified or --rejected")
            else:
                if not getattr(parsed, "verified", False) and not getattr(parsed, "rejected", False):
                    self.error("one of the arguments --verified --rejected is required (or use --interactive)")
                if not getattr(parsed, "note", None):
                    self.error("the following arguments are required: --note")
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = GoateeArgumentParser(
        prog="goatee",
        description="GoATee: Spec and delivery control layer for AI-assisted software changes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize .goatee/project.md in current directory")
    p_init.set_defaults(func=cmd_init)

    # new
    p_new = subparsers.add_parser("new", help="Scaffold a new change contract")
    p_new.add_argument("change_id", help="Alphanumeric identifier for the change (e.g. 001-export-csv)")
    p_new.set_defaults(func=cmd_new)

    # approve
    p_approve = subparsers.add_parser("approve", help="Lock baseline commit after human agreement and budget approval")
    p_approve.add_argument("change_id", nargs="?", help="Identifier of the change to approve (inferred if omitted)")
    p_approve.set_defaults(func=cmd_approve)

    # handoff
    p_handoff = subparsers.add_parser("handoff", help="Render portable handoff contract to stdout")
    p_handoff.add_argument("change_id", nargs="?", help="Identifier of the change to hand off (inferred if omitted)")
    p_handoff.set_defaults(func=cmd_handoff)

    # verify
    p_verify = subparsers.add_parser("verify", help="Execute deterministic verification and compile receipt.md")
    p_verify.add_argument("change_id", nargs="?", help="Identifier of the change to verify (inferred if omitted)")
    p_verify.set_defaults(func=cmd_verify)

    # check
    p_check = subparsers.add_parser("check", help="Pre-flight budget inspection of working tree changes")
    p_check.add_argument("change_id", nargs="?", help="Identifier of the change to check (inferred if omitted)")
    p_check.set_defaults(func=cmd_check)

    # status
    p_status = subparsers.add_parser("status", help="Show GoATee project or change status")
    p_status.add_argument("change_id", nargs="?", help="Optional change ID to inspect in detail")
    p_status.set_defaults(func=cmd_status)

    # attest
    p_attest = subparsers.add_parser("attest", help="Record human attestation into review.md")
    p_attest.add_argument("change_id", nargs="?", help="Identifier of the change to attest (inferred if omitted)")
    p_attest.add_argument("-i", "--interactive", action="store_true", help="Launch interactive attestation wizard")
    p_attest.add_argument("--criterion", help="Criterion ID to attest (e.g. AC-11)")
    p_attest.add_argument("--note", help="Review rationale note (required in non-interactive mode)")
    action_grp = p_attest.add_mutually_exclusive_group(required=False)
    action_grp.add_argument("--verified", action="store_true", help="Positively attest manual criterion")
    action_grp.add_argument("--rejected", action="store_true", help="Reject criterion or change")
    p_attest.set_defaults(func=cmd_attest)

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="Run standard Model Context Protocol (MCP) server over stdio")
    p_mcp.set_defaults(func=cmd_mcp)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
