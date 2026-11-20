"""Zero-side-effect inspection service for GoATee project and change status."""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from goatee.core.budget import compare_budget, get_git_changed_files, GitInspectionError
from goatee.core.models import ChangeContract
from goatee.core.parser import parse_change_contract, ContractSyntaxError


class ControlStateError(Exception):
    """Raised when control-plane artifacts violate consistency or structure."""
    pass


class ReceiptSyntaxError(Exception):
    """Raised when a delivery receipt is structurally malformed or corrupt."""
    pass


VALID_RECEIPT_VERDICTS: Set[str] = {"PASS", "NEEDS_REVIEW", "FAIL"}
VALID_CRITERION_STATUSES: Set[str] = {"VERIFIED", "FAILED", "NEEDS_REVIEW", "NOT_RUN"}


@dataclass(frozen=True)
class ReceiptRow:
    criterion_id: str
    criterion_type: str
    declared: str
    observed: str
    status: str


@dataclass(frozen=True)
class AttestationRow:
    criterion_id: str
    criterion_type: str
    machine_status: str
    human_attestation: str
    reviewer: Optional[str] = None
    attested_at: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class ReceiptSnapshot:
    change_id: str
    verdict: str
    verified_at: str
    baseline_commit: str
    verified_commit: str
    rows: List[ReceiptRow]
    expected_changes: List[str]
    surface_deviations: List[str]
    protected_changes: List[str]
    mutations: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChangeSummary:
    change_id: str
    agreement_status: str
    budget_status: str
    baseline_commit: Optional[str]
    commit_label: str  # "Candidate" or "Verified"
    commit_sha: Optional[str]
    machine_verdict: str
    human_review: str
    coverage_summary: str
    next_action: str = ""


@dataclass(frozen=True)
class ProjectStatusReport:
    project_name: str
    working_tree: str
    changes: List[ChangeSummary]
    archived_changes: List[ChangeSummary] = field(default_factory=list)


@dataclass(frozen=True)
class ChangeDetailReport:
    change_id: str
    intent: str
    agreement_status: str
    budget_status: str
    baseline_commit: Optional[str]
    working_tree: str
    is_verified: bool
    candidate_commit: Optional[str] = None
    verified_commit: Optional[str] = None
    verified_at: Optional[str] = None
    machine_verdict: str = "NOT_VERIFIED"
    human_review: str = "PENDING"
    acceptance_blocked_reason: Optional[str] = None
    review_updated_at: Optional[str] = None
    latest_reviewer: Optional[str] = None
    receipt_sha256: Optional[str] = None
    budget_snapshot_label: str = ""
    expected_changes: List[str] = field(default_factory=list)
    surface_deviations: List[str] = field(default_factory=list)
    protected_changes: List[str] = field(default_factory=list)
    expected_surface: List[str] = field(default_factory=list)
    criteria_statuses: List[ReceiptRow] = field(default_factory=list)
    attestation_rows: List[AttestationRow] = field(default_factory=list)
    next_action: str = ""
    next_action_category: str = ""


def find_project_root(cwd: Optional[Path] = None) -> Path:
    """Ensure cwd is a valid GoATee project root.

    Raises ControlStateError if .goatee/project.md does not exist.
    """
    root = cwd if cwd else Path.cwd()
    project_md = root / ".goatee" / "project.md"
    if not project_md.is_file():
        raise ControlStateError(
            "Not a GoATee project. Run this command from a project root containing .goatee/project.md."
        )
    return root


def resolve_active_change_id(repo_root: Optional[Path] = None) -> Optional[str]:
    """Resolve active change ID from Git branch, pointer file, or active unverified change.

    Returns None if it cannot be unambiguously determined.
    """
    try:
        root = repo_root or find_project_root()
    except ControlStateError:
        return None

    changes_dir = root / ".goatee" / "changes"
    if not changes_dir.is_dir():
        return None

    # 1. Try Git branch name
    is_trunk = False
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=root
    )
    if res.returncode == 0:
        branch = res.stdout.strip()
        if branch in ("main", "master", "trunk", "HEAD"):
            is_trunk = True
        candidates = [branch]
        for prefix in ("change/", "changes/", "feat/", "feature/"):
            if branch.startswith(prefix):
                candidates.append(branch[len(prefix):])
        for cand in candidates:
            if cand and (changes_dir / cand / "change.md").is_file():
                return cand

    # 2. Try GITHUB_HEAD_REF environment discovery hint (e.g. PR branch in CI)
    # Treated strictly as a discovery hint, not an authorization authority.
    gh_ref = os.environ.get("GITHUB_HEAD_REF", "").strip()
    if gh_ref:
        gh_candidates = [gh_ref]
        for prefix in ("change/", "changes/", "feat/", "feature/"):
            if gh_ref.startswith(prefix):
                gh_candidates.append(gh_ref[len(prefix):])
        for cand in gh_candidates:
            if cand and (changes_dir / cand / "change.md").is_file():
                return cand

    # 3. Try .goatee/.active file
    active_file = root / ".goatee" / ".active"
    if active_file.is_file():
        cand = active_file.read_text(encoding="utf-8").strip()
        if cand and (changes_dir / cand / "change.md").is_file():
            return cand

    # 4. Check unverified changes in .goatee/changes/
    # Invariant: NEVER guess an unverified change when on a trunk branch (main/master/trunk).
    # On trunk, active change must be explicitly specified or discovered via GITHUB_HEAD_REF / .active.
    # On non-trunk feature branches, ONLY resolve if there is EXACTLY ONE unverified change.
    if is_trunk:
        return None

    unverified = []
    for d in changes_dir.iterdir():
        if d.is_dir() and (d / "change.md").is_file() and not (d / "receipt.md").is_file():
            unverified.append(d.name)
    if len(unverified) == 1:
        return unverified[0]

    return None


def parse_project_name(project_md_path: Path) -> str:
    """Extract Project-Name from .goatee/project.md, failing closed if missing or empty."""
    if not project_md_path.is_file():
        raise ControlStateError(
            "Not a GoATee project. Run this command from a project root containing .goatee/project.md."
        )
    content = project_md_path.read_text(encoding="utf-8")
    match = re.search(r"^Project-Name:\s*(.*)$", content, re.MULTILINE)
    if not match:
        raise ControlStateError(
            f"Malformed project metadata in {project_md_path}: missing Project-Name."
        )
    name = match.group(1).strip()
    if not name:
        raise ControlStateError(
            f"Malformed project metadata in {project_md_path}: missing or empty Project-Name."
        )
    return name


def get_working_tree_state(repo_root: Path) -> str:
    """Return CLEAN or DIRTY based on git status --porcelain."""
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res.returncode != 0:
        raise ControlStateError(f"Git inspection failed: {res.stderr.strip()}")
    return "DIRTY" if res.stdout.strip() else "CLEAN"


def resolve_current_head(repo_root: Path) -> str:
    """Return current HEAD commit SHA, failing closed on Git error."""
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res.returncode != 0:
        raise ControlStateError(f"Git inspection failed: {res.stderr.strip()}")
    return res.stdout.strip()


def parse_receipt(receipt_path: Path) -> ReceiptSnapshot:
    """Parse receipt.md into ReceiptSnapshot, strictly validating canonical structure."""
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Receipt not found: {receipt_path}")
    content = receipt_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # 1. Header: # Delivery Receipt: <change-id>
    header_match = re.search(r"^# Delivery Receipt:\s*(.+)$", content, re.MULTILINE)
    if not header_match:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing '# Delivery Receipt: <change-id>' header.")
    change_id = header_match.group(1).strip()

    # 2. Verdict
    verdict_match = re.search(r"^Verdict:\s*(.+)$", content, re.MULTILINE)
    if not verdict_match:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing 'Verdict:' field.")
    verdict = verdict_match.group(1).strip()
    if verdict not in VALID_RECEIPT_VERDICTS:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': unrecognized Verdict '{verdict}'.")

    # 3. Verified-At
    verified_at_match = re.search(r"^Verified-At:\s*(.+)$", content, re.MULTILINE)
    if not verified_at_match:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing 'Verified-At:' field.")
    verified_at = verified_at_match.group(1).strip()

    # 4. Commit Provenance
    if "## Commit Provenance" not in content:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing '## Commit Provenance' section.")
    base_match = re.search(r"^Baseline-Commit:\s*(.+)$", content, re.MULTILINE)
    if not base_match:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing 'Baseline-Commit:' field.")
    baseline_commit = base_match.group(1).strip()

    ver_match = re.search(r"^Verified-Commit:\s*(.+)$", content, re.MULTILINE)
    if not ver_match:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing 'Verified-Commit:' field.")
    verified_commit = ver_match.group(1).strip()

    # 5. Requirement Coverage Matrix
    if "## Requirement Coverage Matrix" not in content:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing '## Requirement Coverage Matrix' section.")

    rows: List[ReceiptRow] = []
    matrix_parts = re.split(r"\n##\s+", content.split("## Requirement Coverage Matrix")[1], maxsplit=1)
    matrix_section = matrix_parts[0] if matrix_parts else ""
    for line in matrix_section.splitlines():
        line = line.strip()
        if not line.startswith("|") or ":---" in line or "Criterion" in line:
            continue
        parts = [p.replace(r"\|", "|").strip() for p in re.split(r"(?<!\\)\|", line)]
        # markdown table line "| **AC-01** | automated | `command` | result | **STATUS** |" has 7 elements when split
        if len(parts) >= 6:
            c_id = parts[1].replace("*", "").strip()
            c_type = parts[2].strip()
            c_decl = parts[3].strip()
            c_obs = parts[4].strip()
            c_status = parts[5].replace("*", "").strip()
            if c_status not in VALID_CRITERION_STATUSES:
                raise ReceiptSyntaxError(
                    f"Malformed receipt in '{receipt_path}': unrecognized criterion status '{c_status}' for '{c_id}'."
                )
            rows.append(ReceiptRow(
                criterion_id=c_id,
                criterion_type=c_type,
                declared=c_decl,
                observed=c_obs,
                status=c_status
            ))

    if not rows:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': no criteria parsed in Requirement Coverage Matrix.")

    # 6. Build Budget & Surface Analysis
    if "## Build Budget & Surface Analysis" not in content:
        raise ReceiptSyntaxError(f"Malformed receipt in '{receipt_path}': missing '## Build Budget & Surface Analysis' section.")

    def extract_sublist(section_name: str, text: str) -> List[str]:
        pattern = rf"###\s+{re.escape(section_name)}[^\n]*\n(.*?)(?=\n###|\n##\s+|\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            return []
        items = []
        for l in m.group(1).splitlines():
            l = l.strip()
            if l.startswith("- "):
                val = l[2:].strip()
                if val != "(none)":
                    items.append(val)
        return items

    budget_parts = re.split(r"\n##\s+", content.split("## Build Budget & Surface Analysis")[1], maxsplit=1)
    budget_section = budget_parts[0] if budget_parts else ""
    expected_changes = extract_sublist("Expected Changes", budget_section)
    surface_deviations = extract_sublist("Surface Deviations", budget_section)
    protected_changes = extract_sublist("Protected Changes", budget_section)

    mutations: List[str] = []
    if "## Git-Visible Mutation Report" in content:
        mut_parts = re.split(r"\n##\s+", content.split("## Git-Visible Mutation Report")[1], maxsplit=1)
        mut_section = mut_parts[0] if mut_parts else ""
        if "### Git-Visible Mutations Detected" in mut_section:
            for l in mut_section.splitlines():
                l = l.strip()
                if l.startswith("- "):
                    val = l[2:].strip()
                    if val and not val.startswith("Zero Git-visible"):
                        mutations.append(val)

    return ReceiptSnapshot(
        change_id=change_id,
        verdict=verdict,
        verified_at=verified_at,
        baseline_commit=baseline_commit,
        verified_commit=verified_commit,
        rows=rows,
        expected_changes=expected_changes,
        surface_deviations=surface_deviations,
        protected_changes=protected_changes,
        mutations=mutations,
    )


def validate_contract_receipt_consistency(
    change_dir: Path,
    contract: ChangeContract,
    receipt: ReceiptSnapshot
) -> None:
    """Cross-validate change.md against receipt.md, failing closed if inconsistent."""
    change_id = contract.change_id

    # A. Change identity
    if receipt.change_id != change_id or change_dir.name != change_id:
        raise ControlStateError(
            f"Inconsistent control state for '{change_id}': receipt change ID '{receipt.change_id}' does not match contract '{change_id}'."
        )

    # B. Baseline identity
    contract_base = contract.contract_state.baseline_commit
    if not contract_base or not receipt.baseline_commit or contract_base != receipt.baseline_commit:
        raise ControlStateError(
            f"Inconsistent control state for '{change_id}': receipt Baseline-Commit does not match contract Baseline-Commit."
        )

    # C. Acceptance-criterion identity
    contract_ac_ids = [ac.id for ac in contract.acceptance_criteria]
    receipt_ac_ids = [r.criterion_id for r in receipt.rows]

    # Check duplicates in receipt
    if len(receipt_ac_ids) != len(set(receipt_ac_ids)):
        raise ControlStateError(
            f"Inconsistent control state for '{change_id}': receipt contains duplicate criterion IDs."
        )

    # Check set equality
    if set(contract_ac_ids) != set(receipt_ac_ids):
        raise ControlStateError(
            f"Inconsistent control state for '{change_id}': receipt criteria set does not match contract criteria."
        )

    # Check type equality
    receipt_row_map = {r.criterion_id: r for r in receipt.rows}
    for ac in contract.acceptance_criteria:
        rec_row = receipt_row_map.get(ac.id)
        if rec_row and ac.evidence_type != rec_row.criterion_type:
            raise ControlStateError(
                f"Error: Criterion '{ac.id}' type differs between change contract and delivery receipt. "
                "Review cannot continue against inconsistent evidence."
            )

    # D. Verified commit non-empty
    if not receipt.verified_commit:
        raise ControlStateError(
            f"Inconsistent control state for '{change_id}': receipt Verified-Commit is missing or empty."
        )


def compute_next_action(
    report: ChangeDetailReport,
    contract: Optional[ChangeContract],
    repo_root: Path
) -> Tuple[str, str]:
    """Compute deterministic lifecycle-aware next action and its category."""
    # 1. Landed or archived
    if contract and getattr(contract, "contract_state", None):
        lifecycle = getattr(contract.contract_state, "lifecycle_status", None)
        if lifecycle in ("LANDED", "ARCHIVED"):
            return "Completed", f"Change '{report.change_id}' is {lifecycle.lower()} and archived in repository history."

    archive_dir = repo_root / ".goatee" / "archive" / report.change_id
    if archive_dir.is_dir() and (archive_dir / "change.md").is_file():
        return "Completed", f"Change '{report.change_id}' is archived in repository history."

    # 2. DRAFT state (Baseline not locked)
    if report.agreement_status != "APPROVED" or report.budget_status != "APPROVED" or not report.baseline_commit:
        return "Human Gate", f"Review contract and budget in .goatee/changes/{report.change_id}/change.md; set Agreement-Status and Budget-Status to APPROVED, then run 'goatee approve {report.change_id}'."

    # 3. APPROVED state (Candidate / In Progress, pre-verification)
    if not report.is_verified:
        if report.surface_deviations or report.protected_changes:
            return "Blocked State", f"Unapproved budget deviations detected. Run 'goatee check {report.change_id}' to inspect, or 'goatee amend {report.change_id} --add-surface <path> --note <note>' if expansion is authorized."
        if report.working_tree == "DIRTY":
            return "Machine Action", f"Implementation in progress. Test budget with 'goatee check {report.change_id}'; commit candidate changes and run 'goatee verify {report.change_id}'."
        return "Machine Action", f"Implement changes within approved expected surface. Test with 'goatee check {report.change_id}'; commit candidate and run 'goatee verify {report.change_id}'."

    # 4. Machine verification failed
    if report.machine_verdict == "FAIL" or report.acceptance_blocked_reason:
        return "Blocked State", f"Machine verification failed. Inspect receipt in .goatee/changes/{report.change_id}/receipt.md, fix implementation, commit, and re-run 'goatee verify {report.change_id}'."

    # 5. Machine verification clean (NEEDS_REVIEW or PASS), but human review rejected or pending
    has_manual_criteria = any(r.criterion_type == "manual" for r in report.criteria_statuses)
    if report.human_review == "REJECTED":
        return "Blocked State", f"Human review rejected. Review feedback in .goatee/changes/{report.change_id}/review.md, address concerns in implementation, commit, and re-run 'goatee verify {report.change_id}'."

    if has_manual_criteria and report.human_review != "ACCEPTED":
        return "Human Gate", f"Machine verification clean. Human attestation required: run 'goatee attest {report.change_id} -i' or 'goatee attest {report.change_id} --decision <criterion_id>=<VERIFIED|REJECTED>:<note>'."

    # 6. Accepted delivery (Machine PASS with zero manual criteria, or machine-clean with human ACCEPTED)
    if (report.machine_verdict == "PASS" and not has_manual_criteria) or report.human_review == "ACCEPTED":
        return "Human / CI Gate", f"Delivery contract satisfied. Open/merge pull request into main preserving commit ancestry. After merging to main, run 'goatee land {report.change_id}' on trunk."

    return "Human Gate", f"Review status of change '{report.change_id}'."


def get_change_report(
    repo_root: Path,
    change_id: str,
    working_tree: str,
    current_head: str
) -> ChangeDetailReport:
    """Build a detailed ChangeDetailReport for a single change (active or archived)."""
    change_dir = repo_root / ".goatee" / "changes" / change_id
    change_md_path = change_dir / "change.md"

    if not change_dir.is_dir() or not change_md_path.is_file():
        archive_dir = repo_root / ".goatee" / "archive" / change_id
        if archive_dir.is_dir() and (archive_dir / "change.md").is_file():
            change_dir = archive_dir
            change_md_path = archive_dir / "change.md"
        else:
            raise ControlStateError(f"Change '{change_id}' not found.")

    try:
        contract = parse_change_contract(change_md_path)
    except ContractSyntaxError as e:
        raise ControlStateError(f"Malformed contract in '{change_md_path}': {e}")

    # Extract intent from contract text
    content = change_md_path.read_text(encoding="utf-8")
    intent = ""
    intent_match = re.search(r"## Intent\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if intent_match:
        intent = intent_match.group(1).strip()

    receipt_path = change_dir / "receipt.md"
    if receipt_path.is_file():
        receipt = parse_receipt(receipt_path)
        validate_contract_receipt_consistency(change_dir, contract, receipt)

        from goatee.core.attest import load_and_validate_review
        review = load_and_validate_review(change_dir, contract, receipt, repo_root)

        if review:
            human_review = review.verdict
            review_updated_at = review.updated_at
            latest_reviewer = (
                f"{review.events[-1].reviewer_name} <{review.events[-1].reviewer_email}> ({review.events[-1].attribution} attribution)"
                if review.events else None
            )
            receipt_sha256 = review.receipt_sha256
            attestation_rows = review.rows
        else:
            human_review = "PENDING"
            review_updated_at = None
            latest_reviewer = None
            receipt_sha256 = None
            attestation_rows = []

        acceptance_blocked_reason = None
        if human_review == "PENDING":
            flawed_machine = (
                receipt.verdict == "FAIL"
                or any(r.status in ("FAILED", "NOT_RUN") for r in receipt.rows if r.criterion_type == "automated")
                or len(receipt.surface_deviations) > 0
                or len(receipt.protected_changes) > 0
                or len(receipt.mutations) > 0
            )
            if flawed_machine:
                acceptance_blocked_reason = "Acceptance blocked by machine evidence."

        initial_report = ChangeDetailReport(
            change_id=change_id,
            intent=intent,
            agreement_status=contract.contract_state.agreement_status,
            budget_status=contract.contract_state.budget_status,
            baseline_commit=contract.contract_state.baseline_commit,
            working_tree=working_tree,
            is_verified=True,
            verified_commit=receipt.verified_commit,
            verified_at=receipt.verified_at,
            machine_verdict=receipt.verdict,
            human_review=human_review,
            acceptance_blocked_reason=acceptance_blocked_reason,
            review_updated_at=review_updated_at,
            latest_reviewer=latest_reviewer,
            receipt_sha256=receipt_sha256,
            expected_changes=receipt.expected_changes,
            surface_deviations=receipt.surface_deviations,
            protected_changes=receipt.protected_changes,
            expected_surface=contract.build_budget.expected_surface,
            criteria_statuses=receipt.rows,
            attestation_rows=attestation_rows,
        )
        cat, action = compute_next_action(initial_report, contract, repo_root)
        return ChangeDetailReport(
            change_id=initial_report.change_id,
            intent=initial_report.intent,
            agreement_status=initial_report.agreement_status,
            budget_status=initial_report.budget_status,
            baseline_commit=initial_report.baseline_commit,
            working_tree=initial_report.working_tree,
            is_verified=initial_report.is_verified,
            verified_commit=initial_report.verified_commit,
            verified_at=initial_report.verified_at,
            machine_verdict=initial_report.machine_verdict,
            human_review=initial_report.human_review,
            acceptance_blocked_reason=initial_report.acceptance_blocked_reason,
            review_updated_at=initial_report.review_updated_at,
            latest_reviewer=initial_report.latest_reviewer,
            receipt_sha256=initial_report.receipt_sha256,
            expected_changes=initial_report.expected_changes,
            surface_deviations=initial_report.surface_deviations,
            protected_changes=initial_report.protected_changes,
            expected_surface=initial_report.expected_surface,
            criteria_statuses=initial_report.criteria_statuses,
            attestation_rows=initial_report.attestation_rows,
            next_action=action,
            next_action_category=cat,
        )
    else:
        # Unverified change: pre-verification candidate budget inspection
        baseline = contract.contract_state.baseline_commit
        if baseline:
            try:
                changed_files = get_git_changed_files(baseline, "HEAD", cwd=repo_root)
                comp = compare_budget(changed_files, contract.build_budget)
                exp_changes = comp.expected_changes
                devs = comp.surface_deviations
                prot = comp.protected_changes
                budget_label = "committed candidate only"
            except GitInspectionError as e:
                raise ControlStateError(f"Git inspection failed during budget comparison: {e}")
        else:
            exp_changes = []
            devs = []
            prot = []
            budget_label = "UNAVAILABLE — baseline not locked"

        pending_rows = [
            ReceiptRow(
                criterion_id=ac.id,
                criterion_type=ac.evidence_type,
                declared="",
                observed="",
                status="PENDING_VERIFICATION"
            )
            for ac in contract.acceptance_criteria
        ]

        initial_report = ChangeDetailReport(
            change_id=change_id,
            intent=intent,
            agreement_status=contract.contract_state.agreement_status,
            budget_status=contract.contract_state.budget_status,
            baseline_commit=contract.contract_state.baseline_commit,
            working_tree=working_tree,
            is_verified=False,
            candidate_commit=current_head,
            machine_verdict="NOT_VERIFIED",
            human_review="PENDING",
            budget_snapshot_label=budget_label,
            expected_changes=exp_changes,
            surface_deviations=devs,
            protected_changes=prot,
            expected_surface=contract.build_budget.expected_surface,
            criteria_statuses=pending_rows,
        )
        cat, action = compute_next_action(initial_report, contract, repo_root)
        return ChangeDetailReport(
            change_id=initial_report.change_id,
            intent=initial_report.intent,
            agreement_status=initial_report.agreement_status,
            budget_status=initial_report.budget_status,
            baseline_commit=initial_report.baseline_commit,
            working_tree=initial_report.working_tree,
            is_verified=initial_report.is_verified,
            candidate_commit=initial_report.candidate_commit,
            machine_verdict=initial_report.machine_verdict,
            human_review=initial_report.human_review,
            budget_snapshot_label=initial_report.budget_snapshot_label,
            expected_changes=initial_report.expected_changes,
            surface_deviations=initial_report.surface_deviations,
            protected_changes=initial_report.protected_changes,
            expected_surface=initial_report.expected_surface,
            criteria_statuses=initial_report.criteria_statuses,
            next_action=action,
            next_action_category=cat,
        )


def _build_change_summary(
    root: Path,
    cdir: Path,
    working_tree: str,
    current_head: str
) -> ChangeSummary:
    detail = get_change_report(root, cdir.name, working_tree, current_head)

    if detail.is_verified:
        commit_label = "Verified"
        commit_sha = detail.verified_commit[:7] if detail.verified_commit else "(none)"
        verified_count = sum(1 for r in detail.criteria_statuses if r.status == "VERIFIED")
        total_count = len(detail.criteria_statuses)
        coverage = f"{verified_count}/{total_count} VERIFIED"
    else:
        commit_label = "Candidate"
        commit_sha = current_head[:7] if current_head else "(none)"
        coverage = "PENDING"

    base_sha = detail.baseline_commit[:7] if detail.baseline_commit else "(none)"

    return ChangeSummary(
        change_id=detail.change_id,
        agreement_status=detail.agreement_status,
        budget_status=detail.budget_status,
        baseline_commit=base_sha,
        commit_label=commit_label,
        commit_sha=commit_sha,
        machine_verdict=detail.machine_verdict,
        human_review=detail.human_review,
        coverage_summary=coverage,
        next_action=detail.next_action,
    )


def get_project_status(repo_root: Optional[Path] = None) -> ProjectStatusReport:
    """Build ProjectStatusReport covering active changes under .goatee/changes/ and archived in .goatee/archive/."""
    root = find_project_root(repo_root)
    project_name = parse_project_name(root / ".goatee" / "project.md")
    working_tree = get_working_tree_state(root)
    current_head = resolve_current_head(root)

    changes_dir = root / ".goatee" / "changes"
    summaries: List[ChangeSummary] = []
    if changes_dir.is_dir():
        change_dirs = sorted([d for d in changes_dir.iterdir() if d.is_dir()], key=lambda d: d.name)
        for cdir in change_dirs:
            change_md = cdir / "change.md"
            if not change_md.is_file():
                raise ControlStateError(f"Incomplete change directory '{cdir}' missing change.md.")
            summaries.append(_build_change_summary(root, cdir, working_tree, current_head))

    archived_summaries: List[ChangeSummary] = []
    archive_dir = root / ".goatee" / "archive"
    if archive_dir.is_dir():
        arch_dirs = sorted([d for d in archive_dir.iterdir() if d.is_dir()], key=lambda d: d.name)
        for adir in arch_dirs:
            if (adir / "change.md").is_file():
                archived_summaries.append(_build_change_summary(root, adir, working_tree, current_head))

    return ProjectStatusReport(
        project_name=project_name,
        working_tree=working_tree,
        changes=summaries,
        archived_changes=archived_summaries,
    )


def format_project_overview(report: ProjectStatusReport, show_archived: bool = False) -> str:
    """Format ProjectStatusReport as plain-text CLI output."""
    lines = [
        f"Project: {report.project_name}",
        f"Working Tree: {report.working_tree}",
        "",
        "Changes:"
    ]
    if not report.changes:
        lines.append("  (no active changes found)")
    else:
        for c in report.changes:
            lines.append(f"- {c.change_id}")
            lines.append(f"  Agreement: {c.agreement_status}")
            lines.append(f"  Budget: {c.budget_status}")
            lines.append(f"  Baseline: {c.baseline_commit}")
            lines.append(f"  {c.commit_label}: {c.commit_sha}")
            lines.append(f"  Machine: {c.machine_verdict}")
            lines.append(f"  Human: {c.human_review}")
            lines.append(f"  Coverage: {c.coverage_summary}")
            if c.next_action:
                lines.append(f"  Next: {c.next_action}")
            lines.append("")

    if len(report.changes) > 1:
        lines.append("Notice: Multiple active changes present. Specify change ID to inspect details: 'goatee status <change-id>'.\n")

    if report.archived_changes:
        if show_archived:
            lines.append("Archived Changes:")
            for c in report.archived_changes:
                lines.append(f"- {c.change_id}")
                lines.append(f"  Agreement: {c.agreement_status}")
                lines.append(f"  Budget: {c.budget_status}")
                lines.append(f"  Baseline: {c.baseline_commit}")
                lines.append(f"  {c.commit_label}: {c.commit_sha}")
                lines.append(f"  Machine: {c.machine_verdict}")
                lines.append(f"  Human: {c.human_review}")
                lines.append(f"  Coverage: {c.coverage_summary}")
                lines.append("")
        else:
            lines.append(f"Archived Changes: {len(report.archived_changes)} (use --all to inspect)")

    return "\n".join(lines).rstrip()


def format_change_detail(report: ChangeDetailReport, contract: Optional[ChangeContract] = None) -> str:
    """Format ChangeDetailReport as plain-text CLI output."""
    lines = [
        f"# Change: {report.change_id}",
        "",
        "Intent:",
        report.intent,
        "",
        "Contract State:",
        f"Agreement-Status: {report.agreement_status}",
        f"Budget-Status: {report.budget_status}",
        f"Baseline-Commit: {report.baseline_commit if report.baseline_commit else '(none)'}",
        "",
        f"Working Tree: {report.working_tree}",
        ""
    ]

    if report.next_action:
        lines.append(f"Next Action: [{report.next_action_category}] {report.next_action}")
        lines.append("")

    if report.is_verified:
        lines.append("Evidence State: (receipt snapshot)")
        lines.append(f"Verified-Commit: {report.verified_commit}")
        lines.append(f"Verified-At: {report.verified_at}")
        lines.append(f"Machine Verdict: {report.machine_verdict}")
        lines.append(f"Human Review: {report.human_review}")
        if report.acceptance_blocked_reason:
            lines.append(f"  ({report.acceptance_blocked_reason})")
        if report.review_updated_at:
            lines.append(f"Review Updated-At: {report.review_updated_at}")
            lines.append(f"Latest Reviewer: {report.latest_reviewer}")
            lines.append(f"Receipt-SHA256: {report.receipt_sha256}")
        lines.append("")
        lines.append("Requirement Coverage:")
        att_map = {r.criterion_id: r for r in report.attestation_rows}
        for r in report.criteria_statuses:
            lines.append(f"- {r.criterion_id}: [{r.criterion_type}]")
            lines.append(f"  Machine Status:    {r.status}")
            if r.criterion_type == "manual":
                att = att_map.get(r.criterion_id)
                if att and att.human_attestation != "PENDING":
                    rev_txt = (
                        f" (attested by {att.reviewer})"
                        if att.reviewer and att.human_attestation == "VERIFIED"
                        else (f" (rejected by {att.reviewer})" if att.reviewer and att.human_attestation == "REJECTED" else "")
                    )
                    lines.append(f"  Human Attestation: {att.human_attestation}{rev_txt}")
                    if att.note:
                        lines.append(f"  Note: {att.note}")
                else:
                    lines.append("  Human Attestation: PENDING")
            elif r.criterion_type == "automated":
                att = att_map.get(r.criterion_id)
                if att and att.human_attestation == "REJECTED":
                    rev_txt = f" (disagreed by {att.reviewer})" if att.reviewer else ""
                    lines.append(f"  Human Attestation: REJECTED{rev_txt}")
                    if att.note:
                        lines.append(f"  Note: {att.note}")
        lines.append("")
        lines.append("Build Budget & Surface Analysis:")
        lines.append(f"Expected Changes: {len(report.expected_changes)}")
        if report.expected_changes:
            for p in report.expected_changes:
                lines.append(f"- {p}")
        lines.append(f"Surface Deviations: {'(none)' if not report.surface_deviations else ', '.join(report.surface_deviations)}")
        lines.append(f"Protected Changes: {'(none)' if not report.protected_changes else ', '.join(report.protected_changes)}")
    else:
        lines.append("Evidence State:")
        lines.append(f"Candidate-Commit: {report.candidate_commit}")
        lines.append(f"Machine Verdict: {report.machine_verdict}")
        lines.append(f"Human Review: {report.human_review}")
        lines.append("")
        if report.budget_snapshot_label == "UNAVAILABLE — baseline not locked":
            lines.append("Build Budget Snapshot:")
            lines.append("UNAVAILABLE — baseline not locked")
        else:
            lines.append(f"Build Budget Snapshot: ({report.budget_snapshot_label})")
            lines.append(f"Expected Changes: {len(report.expected_changes)}")
            if report.expected_changes:
                for p in report.expected_changes:
                    lines.append(f"- {p}")
            lines.append(f"Surface Deviations: {'(none)' if not report.surface_deviations else ', '.join(report.surface_deviations)}")
            lines.append(f"Protected Changes: {'(none)' if not report.protected_changes else ', '.join(report.protected_changes)}")
        lines.append("")
        lines.append("Expected Surface:")
        for p in report.expected_surface:
            lines.append(f"- {p}")
        lines.append("")
        lines.append("Acceptance Criteria:")
        for r in report.criteria_statuses:
            desc = ""
            if contract:
                for ac in contract.acceptance_criteria:
                    if ac.id == r.criterion_id:
                        desc = ac.description
                        break
            if desc:
                lines.append(f"- {r.criterion_id}: [{r.criterion_type}] {r.status}\n  {desc}")
            else:
                lines.append(f"- {r.criterion_id}: [{r.criterion_type}] {r.status}")

    return "\n".join(lines).rstrip()
