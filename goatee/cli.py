"""Command line interface for GoATee v0.1 Phase 1."""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
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
    active_file = cwd / ".goatee" / ".active"
    try:
        active_file.write_text(args.change_id + "\n", encoding="utf-8")
    except OSError:
        pass
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
        from goatee.core.budget import unquote_git_path
        path_part = unquote_git_path(path_part)
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

    # 5. Record baseline commit and contract hash in change.md
    from goatee.core.parser import compute_contract_hash
    update_contract_state(change_file, "Baseline-Commit", baseline_sha)
    c_hash = compute_contract_hash(change_file.read_text(encoding="utf-8"))
    update_contract_state(change_file, "Contract-SHA256", c_hash)
    print(f"Approved change '{change_id}' and locked baseline commit: {baseline_sha} (contract hash: {c_hash[:8]})")
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
            "\n".join(f"  {c}" for c in uncommitted) +
            f"\n\nPre-flight check: Run 'goatee check' (or 'goatee check {change_id}') to inspect budget compliance of uncommitted changes without committing.",
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

    # Check contract tamper integrity
    from goatee.core.parser import compute_contract_hash, read_contract_hash
    locked_hash = read_contract_hash(change_file)
    if locked_hash:
        current_hash = compute_contract_hash(change_file.read_text(encoding="utf-8"))
        if current_hash != locked_hash:
            print(
                f"CONTRACT INTEGRITY VIOLATION:\n"
                f"Change contract '{change_id}' has been modified after human approval.\n"
                f"Approved hash: {locked_hash}\n"
                f"Current hash:  {current_hash}\n"
                "Verification halted closed: unauthorized agreement or budget modification.",
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

    json_mode = getattr(args, "json", False)
    if json_mode:
        print(f"Verification complete for '{change_id}'.", file=sys.stderr)
        print(f"Verdict: {result.verdict}", file=sys.stderr)
        print(f"Delivery receipt compiled at {receipt_file}", file=sys.stderr)
    else:
        print(f"Verification complete for '{change_id}'.")
        print(f"Verdict: {result.verdict}")
        print(f"Delivery receipt compiled at {receipt_file}")

    if json_mode:
        import json
        payload = {
            "change_id": change_id,
            "verdict": result.verdict,
            "verified_at": result.verified_at,
            "baseline_commit": result.baseline_commit,
            "verified_commit": result.verified_commit,
            "matrix": [
                {
                    "criterion_id": m.criterion_id,
                    "criterion_type": m.evidence_type,
                    "declared": m.declared,
                    "observed": m.observed,
                    "status": m.status,
                }
                for m in result.criterion_results
            ],
            "expected_changes": result.budget_result.expected_changes,
            "surface_deviations": result.budget_result.surface_deviations,
            "protected_changes": result.budget_result.protected_changes,
            "mutations": result.mutations,
        }
        print(json.dumps(payload, indent=2))
        if result.verdict == "FAIL":
            from goatee.core.verifier import format_failure_diagnostic
            show_diag = getattr(args, "diagnostics", False)
            diag_output = format_failure_diagnostic(result, show_details=show_diag)
            if diag_output:
                print(diag_output, file=sys.stderr)
    elif result.verdict == "FAIL":
        from goatee.core.verifier import format_failure_diagnostic
        show_diag = getattr(args, "diagnostics", False)
        diag_output = format_failure_diagnostic(result, show_details=show_diag)
        if diag_output:
            print(diag_output, file=sys.stderr)

    if getattr(args, "ci", False):
        from goatee.core.ci import write_github_step_summary
        from goatee.core.exporter import generate_pr_comment_markdown
        from goatee.core.status import get_change_report, get_working_tree_state, resolve_current_head, parse_project_name
        working_tree = get_working_tree_state(cwd)
        current_head = resolve_current_head(cwd)
        report = get_change_report(cwd, change_id, working_tree, current_head)
        pname = parse_project_name(cwd / ".goatee" / "project.md")
        step_summary = generate_pr_comment_markdown(report, contract, pname)
        write_github_step_summary(step_summary)

    if getattr(args, "allow_needs_review", False) and result.verdict == "NEEDS_REVIEW":
        return 0

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
            if not contract_path.is_file():
                contract_path = root / ".goatee" / "archive" / args.change_id / "change.md"
            contract = parse_change_contract(contract_path) if contract_path.is_file() else None
            output = format_change_detail(change_report, contract)
            print(output)
            return 0
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        try:
            show_archived = getattr(args, "all", False) or getattr(args, "archived", False)
            project_report = get_project_status(root)
            output = format_project_overview(project_report, show_archived=show_archived)
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

    if getattr(args, "decision", None):
        from goatee.core.attest import record_multi_attestation
        try:
            count = record_multi_attestation(cwd, change_id, args.decision)
            print(f"Recorded {count} explicit attestation decision(s) for change '{change_id}' in .goatee/changes/{change_id}/review.md")
            print("Notice: Commit review.md to preserve these review records in Git history.")
            return 0
        except (ControlStateError, ReceiptSyntaxError, ContractSyntaxError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

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


def cmd_amend(args: argparse.Namespace) -> int:
    """Legitimately amend an approved Build Budget by adding surface with human attribution and rationale."""
    try:
        change_id = resolve_or_fail_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not args.add_surface:
        print("Error: --add-surface <path> is required to amend budget.", file=sys.stderr)
        return 1

    if not args.note or not args.note.strip():
        print("Error: --note <rationale> is required for budget amendment.", file=sys.stderr)
        return 1

    from goatee.core.attest import normalize_note, get_git_reviewer, ControlStateError
    try:
        clean_note = normalize_note(args.note)
    except ControlStateError as cse:
        print(f"Error: {cse}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_file = cwd / ".goatee" / "changes" / change_id / "change.md"
    if not change_file.is_file():
        print(f"Error: Change contract not found at {change_file}", file=sys.stderr)
        return 1

    content = change_file.read_text(encoding="utf-8")
    try:
        contract = parse_change_contract(change_file)
    except ContractSyntaxError as cse:
        print(f"Contract syntax error:\n{cse}", file=sys.stderr)
        return 1

    if not contract.contract_state.baseline_commit:
        print(f"Error: Change '{change_id}' is not yet approved. Edit change.md directly before approval.", file=sys.stderr)
        return 1

    try:
        reviewer_name, reviewer_email = get_git_reviewer(cwd)
    except Exception:
        reviewer_name, reviewer_email = "Unknown", "unknown@localhost"

    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    new_surface_path = args.add_surface.strip()
    lines = content.splitlines()

    # Remove from ### Blast Radius if present
    in_blast_radius = False
    cleaned_lines = []
    for line in lines:
        if line.strip() == "### Blast Radius":
            in_blast_radius = True
            cleaned_lines.append(line)
            continue
        if in_blast_radius:
            if line.startswith("### ") or line.startswith("## ") or (":" in line and not line.startswith(" ")):
                in_blast_radius = False
            elif line.strip() == f"- {new_surface_path}":
                continue
        cleaned_lines.append(line)
    lines = cleaned_lines

    # Insert under ### Expected Surface
    new_lines = []
    inserted_surface = False
    in_exp_surface = False

    for line in lines:
        if line.strip() == "### Expected Surface":
            in_exp_surface = True
            new_lines.append(line)
            continue

        if in_exp_surface:
            if line.startswith("### ") or line.startswith("## ") or (":" in line and not line.startswith(" ")):
                if not inserted_surface:
                    new_lines.append(f"- {new_surface_path}")
                    inserted_surface = True
                in_exp_surface = False

        new_lines.append(line)

    if in_exp_surface and not inserted_surface:
        new_lines.append(f"- {new_surface_path}")
        inserted_surface = True

    content_with_surface = "\n".join(new_lines)

    # Append to ## Amendments section
    amend_entry = f"- [{now_utc}] Added surface '{new_surface_path}' (Attributed to {reviewer_name} <{reviewer_email}>; Reason: {clean_note})"
    if "## Amendments" in content_with_surface:
        content_with_surface = content_with_surface.replace("## Amendments", f"## Amendments\n{amend_entry}")
    else:
        state_header = "## Contract State"
        if state_header in content_with_surface:
            content_with_surface = content_with_surface.replace(state_header, f"## Amendments\n{amend_entry}\n\n{state_header}")
        else:
            content_with_surface += f"\n\n## Amendments\n{amend_entry}\n"

    change_file.write_text(content_with_surface + "\n", encoding="utf-8")

    # Re-compute and update Contract-SHA256
    from goatee.core.parser import compute_contract_hash, update_contract_state
    new_hash = compute_contract_hash(change_file.read_text(encoding="utf-8"))
    update_contract_state(change_file, "Contract-SHA256", new_hash)

    print(f"Amended Build Budget for '{change_id}': added '{new_surface_path}' to Expected Surface.")
    print(f"New Contract-SHA256 locked: {new_hash[:8]}")
    print("Notice: Commit change.md to lock this amendment in Git history.")
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """Manage Git pre-commit guardrail hooks."""
    cwd = Path.cwd()
    res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print("Error: Not inside a valid Git repository.", file=sys.stderr)
        return 1

    git_dir_res = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, cwd=cwd)
    git_dir = cwd / git_dir_res.stdout.strip()
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit_path = hooks_dir / "pre-commit"

    HOOK_MARKER = "# GoATee Pre-Commit Guardrail"
    HOOK_INVOCATION = "if command -v goatee >/dev/null 2>&1; then goatee check; else python3 -m goatee.cli check; fi"
    HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
# Automatically check uncommitted working tree against approved GoATee Build Budget
{HOOK_INVOCATION}
"""

    action = getattr(args, "hook_action", "")

    if action == "install":
        import stat
        if pre_commit_path.is_file():
            content = pre_commit_path.read_text(encoding="utf-8")
            if HOOK_MARKER in content:
                print(f"GoATee pre-commit hook is already installed at {pre_commit_path}.")
                return 0
            pre_commit_path.write_text(content.rstrip() + f"\n\n{HOOK_MARKER}\n{HOOK_INVOCATION}\n", encoding="utf-8")
        else:
            pre_commit_path.write_text(HOOK_SCRIPT, encoding="utf-8")

        pre_commit_path.chmod(pre_commit_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Installed GoATee pre-commit guardrail hook at {pre_commit_path}.")
        return 0

    elif action == "uninstall":
        if not pre_commit_path.is_file():
            print("No pre-commit hook installed.", file=sys.stderr)
            return 0

        content = pre_commit_path.read_text(encoding="utf-8")
        if HOOK_MARKER not in content:
            print("No GoATee guardrail found in pre-commit hook.", file=sys.stderr)
            return 0

        if content.strip() == HOOK_SCRIPT.strip():
            pre_commit_path.unlink()
            print(f"Uninstalled GoATee pre-commit hook from {pre_commit_path}.")
        else:
            cleaned = "\n".join(
                l for l in content.splitlines()
                if HOOK_MARKER not in l and "python3 -m goatee.cli check" not in l
            )
            pre_commit_path.write_text(cleaned.strip() + "\n", encoding="utf-8")
            print(f"Removed GoATee guardrail from {pre_commit_path}.")
        return 0

    else:
        print(f"Unknown hook action: {action}", file=sys.stderr)
        return 1


def cmd_lint(args: argparse.Namespace) -> int:
    """Semantic and structural contract linter."""
    from goatee.core.status import find_project_root, ControlStateError
    from goatee.core.linter import lint_change_contract, format_lint_report

    try:
        change_id = resolve_or_fail_change_id(args.change_id)
        root = find_project_root()
    except (ValueError, ControlStateError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    contract_path = root / ".goatee" / "changes" / change_id / "change.md"
    if not contract_path.is_file():
        contract_path = root / ".goatee" / "archive" / change_id / "change.md"

    issues = lint_change_contract(contract_path)
    report = format_lint_report(change_id, issues)
    print(report)

    has_errors = any(i.severity == "ERROR" for i in issues)
    return 1 if has_errors else 0


def cmd_land(args: argparse.Namespace) -> int:
    """Land an accepted change into project history and archive its control records."""
    from goatee.core.status import find_project_root, ControlStateError
    from goatee.core.lifecycle import land_change, LifecycleError

    try:
        change_id = resolve_or_fail_change_id(args.change_id)
        root = find_project_root()
        dest = land_change(root, change_id)
        print(f"Successfully landed change '{change_id}'.")
        print(f"Archived control records to {dest}")
        print("Notice: Commit .goatee/ in Git to record this landing in repository history.")
        return 0
    except (ValueError, ControlStateError, LifecycleError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_archive(args: argparse.Namespace) -> int:
    """Explicitly archive a change contract."""
    from goatee.core.status import find_project_root, ControlStateError
    from goatee.core.lifecycle import archive_change, LifecycleError

    try:
        change_id = resolve_or_fail_change_id(args.change_id)
        root = find_project_root()
        reason = getattr(args, "reason", None) or "ARCHIVED"
        dest = archive_change(root, change_id, reason=reason)
        print(f"Archived change '{change_id}' to {dest} (reason: {reason}).")
        print("Notice: Commit .goatee/ in Git to record this change in repository history.")
        return 0
    except (ValueError, ControlStateError, LifecycleError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_rules(args: argparse.Namespace) -> int:
    """Export native AI agent governance rules."""
    from goatee.core.status import find_project_root, ControlStateError
    from goatee.core.rules import export_agent_rules

    try:
        root = find_project_root()
        target = getattr(args, "target", "all") or "all"
        out_p = Path(args.output) if getattr(args, "output", None) else None
        results = export_agent_rules(root, target=target, custom_output=out_p)
        print(f"Exported GoATee AI delivery rules for target '{target}':")
        for p, ok in results:
            rel = p.relative_to(root) if p.is_relative_to(root) else p
            print(f"  ✓ {rel}")
        return 0
    except (ControlStateError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_export(args: argparse.Namespace) -> int:
    """Export change delivery artifacts, certificates, and compliance reports."""
    from goatee.core.status import find_project_root, ControlStateError
    from goatee.core.exporter import export_certificate, export_pr_comment

    try:
        change_id = resolve_or_fail_change_id(args.change_id)
        root = find_project_root()
        out_p = Path(args.output) if getattr(args, "output", None) else None

        if getattr(args, "pr_comment", False) or getattr(args, "markdown", False):
            target = export_pr_comment(root, change_id, output_path=out_p)
            print(f"Exported PR comment markdown summary for '{change_id}':")
            print(f"  Path: {target}")
            return 0

        target = export_certificate(root, change_id, output_path=out_p)
        print(f"Exported HTML delivery certificate for '{change_id}':")
        print(f"  Path: {target}")
        return 0
    except (ValueError, ControlStateError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_ci(args: argparse.Namespace) -> int:
    """Run GoATee continuous integration pipeline with automated verification, linting, and step summary."""
    import json
    from goatee.core.status import find_project_root, ControlStateError
    from goatee.core.ci import run_ci, ci_result_to_dict, format_github_step_summary

    try:
        root = find_project_root()
    except ControlStateError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    change_id = getattr(args, "change_id", None)
    allow_needs_review = getattr(args, "allow_needs_review", False)
    all_changes = getattr(args, "all", False)
    json_mode = getattr(args, "json", False)
    write_receipt = getattr(args, "write_receipt", False)

    res = run_ci(
        root=root,
        change_id=change_id,
        allow_needs_review=allow_needs_review,
        all_changes=all_changes,
        write_step_summary=True,
        write_receipt=write_receipt,
    )

    if json_mode:
        print(json.dumps(ci_result_to_dict(res), indent=2))
    else:
        print(format_github_step_summary(res))

    if getattr(args, "output_summary", None):
        out_p = Path(args.output_summary)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(format_github_step_summary(res), encoding="utf-8")

    return res.exit_code


class GoateeArgumentParser(argparse.ArgumentParser):
    """Custom argument parser enforcing conditional sub-command validation."""
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args=args, namespace=namespace)
        if getattr(parsed, "command", None) == "attest":
            if getattr(parsed, "decision", None):
                if getattr(parsed, "interactive", False):
                    self.error("cannot combine --decision with --interactive")
                if getattr(parsed, "verified", False) or getattr(parsed, "rejected", False):
                    self.error("cannot combine --decision with --verified or --rejected")
            elif getattr(parsed, "interactive", False):
                if getattr(parsed, "verified", False) or getattr(parsed, "rejected", False):
                    self.error("cannot combine --interactive with --verified or --rejected")
            else:
                if not getattr(parsed, "verified", False) and not getattr(parsed, "rejected", False):
                    self.error("one of the arguments --verified --rejected is required (or use --interactive or --decision)")
                if not getattr(parsed, "note", None):
                    self.error("the following arguments are required: --note")
        return parsed


def build_parser() -> argparse.ArgumentParser:
    from goatee import __version__
    parser = GoateeArgumentParser(
        prog="goatee",
        description="GoATee: Spec and delivery control layer for AI-assisted software changes."
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
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
    p_verify.add_argument("-d", "--diagnostics", action="store_true", help="Include bounded stdout/stderr excerpts in terminal failure output")
    p_verify.add_argument("--ci", action="store_true", help="CI integration mode: writes summary to GITHUB_STEP_SUMMARY if present")
    p_verify.add_argument("--allow-needs-review", action="store_true", help="Permit exit 0 when automated tests pass but manual review is pending")
    p_verify.add_argument("--json", action="store_true", help="Output verification receipt as JSON to stdout")
    p_verify.set_defaults(func=cmd_verify)

    # ci
    p_ci = subparsers.add_parser("ci", help="Execute complete CI governance pipeline (linting, verification, and summary)")
    p_ci.add_argument("change_id", nargs="?", help="Identifier of the change to verify (inferred if omitted)")
    p_ci.add_argument("--all", action="store_true", help="Verify all unarchived approved changes")
    p_ci.add_argument("--allow-needs-review", action="store_true", help="Exit code 0 if automated checks pass and manual review is pending")
    p_ci.add_argument("--write-receipt", action="store_true", help="Write compiled receipt.md to change directory")
    p_ci.add_argument("--json", action="store_true", help="Output CI results as JSON")
    p_ci.add_argument("--output-summary", help="Path to write the markdown summary file")
    p_ci.set_defaults(func=cmd_ci)

    # check
    p_check = subparsers.add_parser("check", help="Pre-flight budget inspection of working tree changes")
    p_check.add_argument("change_id", nargs="?", help="Identifier of the change to check (inferred if omitted)")
    p_check.set_defaults(func=cmd_check)

    # status
    p_status = subparsers.add_parser("status", help="Show GoATee project or change status")
    p_status.add_argument("change_id", nargs="?", help="Optional change ID to inspect in detail")
    p_status.add_argument("--all", action="store_true", help="Include archived changes in project overview")
    p_status.add_argument("--archived", action="store_true", help="Include archived changes in project overview")
    p_status.set_defaults(func=cmd_status)

    # lint
    p_lint = subparsers.add_parser("lint", help="Statically validate change contract syntax, budget conflicts, and security boundaries")
    p_lint.add_argument("change_id", nargs="?", help="Identifier of the change to lint (inferred if omitted)")
    p_lint.set_defaults(func=cmd_lint)

    # land
    p_land = subparsers.add_parser("land", help="Land an accepted change into project history and archive its control records")
    p_land.add_argument("change_id", nargs="?", help="Identifier of the change to land (inferred if omitted)")
    p_land.set_defaults(func=cmd_land)

    # archive
    p_archive = subparsers.add_parser("archive", help="Explicitly archive a change contract")
    p_archive.add_argument("change_id", nargs="?", help="Identifier of the change to archive (inferred if omitted)")
    p_archive.add_argument("--reason", default="ARCHIVED", help="Reason for archival (e.g. SUPERSEDED, ABANDONED)")
    p_archive.set_defaults(func=cmd_archive)

    # rules
    p_rules = subparsers.add_parser("rules", help="Manage native AI coding assistant governance rules")
    rules_sub = p_rules.add_subparsers(dest="rules_action", required=True)
    p_rules_exp = rules_sub.add_parser("export", help="Export or update AI agent rules (CLAUDE.md, .cursorrules, .windsurfrules, .gemini/rules)")
    p_rules_exp.add_argument("--target", default="all", choices=["claude", "cursor", "windsurf", "gemini", "all"], help="Target assistant rule file format (default: all)")
    p_rules_exp.add_argument("--output", help="Custom output file path")
    p_rules_exp.set_defaults(func=cmd_rules)

    # attest
    p_attest = subparsers.add_parser("attest", help="Record human attestation into review.md")
    p_attest.add_argument("change_id", nargs="?", help="Identifier of the change to attest (inferred if omitted)")
    p_attest.add_argument("-i", "--interactive", action="store_true", help="Launch interactive attestation wizard")
    p_attest.add_argument("--decision", action="append", default=[], help="Explicit criterion decision in format <criterion_id>=<VERIFIED|REJECTED>:<note> (repeatable)")
    p_attest.add_argument("--criterion", help="Criterion ID to attest (e.g. AC-11)")
    p_attest.add_argument("--note", help="Review rationale note (required in non-interactive mode)")
    action_grp = p_attest.add_mutually_exclusive_group(required=False)
    action_grp.add_argument("--verified", action="store_true", help="Positively attest manual criterion")
    action_grp.add_argument("--rejected", action="store_true", help="Reject criterion or change")
    p_attest.set_defaults(func=cmd_attest)

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="Run standard Model Context Protocol (MCP) server over stdio")
    p_mcp.set_defaults(func=cmd_mcp)

    # amend
    p_amend = subparsers.add_parser("amend", help="Amend an approved Build Budget with human rationale")
    p_amend.add_argument("change_id", nargs="?", help="Identifier of the change to amend (inferred if omitted)")
    p_amend.add_argument("--add-surface", required=True, help="New file or directory path to add to Expected Surface")
    p_amend.add_argument("--note", required=True, help="Mandatory rationale for expanding the budget")
    p_amend.set_defaults(func=cmd_amend)

    # hook
    p_hook = subparsers.add_parser("hook", help="Manage GoATee Git guardrail hooks")
    hook_sub = p_hook.add_subparsers(dest="hook_action", required=True)
    p_hook_in = hook_sub.add_parser("install", help="Install pre-commit budget guardrail hook")
    p_hook_in.set_defaults(func=cmd_hook)
    p_hook_un = hook_sub.add_parser("uninstall", help="Uninstall pre-commit guardrail hook")
    p_hook_un.set_defaults(func=cmd_hook)

    # export
    p_export = subparsers.add_parser("export", help="Export presentation-ready delivery certificates and artifacts")
    p_export.add_argument("change_id", nargs="?", help="Identifier of the change to export (inferred if omitted)")
    p_export.add_argument("--html", action="store_true", default=True, help="Export as self-contained HTML certificate (default)")
    p_export.add_argument("--pr-comment", "--markdown", dest="pr_comment", action="store_true", help="Export summary as GitHub/GitLab PR comment markdown")
    p_export.add_argument("-o", "--output", help="Custom output file path")
    p_export.set_defaults(func=cmd_export)

    # cert (alias for export)
    p_cert = subparsers.add_parser("cert", help="Generate HTML delivery certificate (alias for export)")
    p_cert.add_argument("change_id", nargs="?", help="Identifier of the change to export (inferred if omitted)")
    p_cert.add_argument("-o", "--output", help="Custom output file path")
    p_cert.set_defaults(func=cmd_export)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
