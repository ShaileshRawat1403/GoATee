"""Command line interface for GoATee v0.1 Phase 1."""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from goatee.core.parser import parse_change_contract, update_contract_state


SAFE_CHANGE_ID_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def validate_change_id(change_id: str) -> None:
    """Validate change-id against safe restricted format to prevent path traversal."""
    if not change_id or not SAFE_CHANGE_ID_REGEX.match(change_id):
        raise ValueError(
            f"Invalid change-id '{change_id}'. "
            "Must start with an alphanumeric character, contain only letters, numbers, hyphens, or underscores, "
            "and be between 1 and 64 characters long with no path separators."
        )


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
        validate_change_id(args.change_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    change_file = cwd / ".goatee" / "changes" / args.change_id / "change.md"

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
    contract = parse_change_contract(change_file)
    if contract.contract_state.baseline_commit:
        print(
            f"Error: Change '{args.change_id}' already has a baseline commit: "
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
    print(f"Approved change '{args.change_id}' and locked baseline commit: {baseline_sha}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
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
    p_approve.add_argument("change_id", help="Identifier of the change to approve")
    p_approve.set_defaults(func=cmd_approve)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
