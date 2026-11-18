"""Continuous Integration (CI) and team governance engine for GoATee."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from goatee.core.budget import GitInspectionError, is_control_artifact
from goatee.core.linter import lint_change_contract, LintIssue
from goatee.core.models import ChangeContract
from goatee.core.parser import (
    parse_change_contract,
    compute_contract_hash,
    read_contract_hash,
    ContractSyntaxError,
)
from goatee.core.status import (
    find_project_root,
    resolve_active_change_id,
    ControlStateError,
    parse_receipt,
    ReceiptSyntaxError,
)
from goatee.core.attest import load_and_validate_review
from goatee.core.verifier import verify_change, render_receipt_markdown, VerificationResult


@dataclass
class ChangeCIReport:
    change_id: str
    status: str  # "PASS", "NEEDS_REVIEW", "FAIL"
    intent: str
    baseline_commit: Optional[str]
    verified_commit: Optional[str]
    lint_issues: List[LintIssue] = field(default_factory=list)
    verification_result: Optional[VerificationResult] = None
    human_verdict: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class CIRunResult:
    overall_status: str  # "PASS", "NEEDS_REVIEW_ALLOWED", "NEEDS_REVIEW", "FAIL"
    exit_code: int
    allow_needs_review: bool
    reports: List[ChangeCIReport] = field(default_factory=list)
    project_root: Path = field(default_factory=Path.cwd)


def run_ci(
    root: Optional[Path] = None,
    change_id: Optional[str] = None,
    allow_needs_review: bool = False,
    all_changes: bool = False,
    write_step_summary: bool = True,
    write_receipt: bool = False,
) -> CIRunResult:
    """Execute complete GoATee CI verification pipeline across changes."""
    project_root = root or find_project_root()
    changes_dir = project_root / ".goatee" / "changes"

    target_change_ids: List[str] = []
    if change_id:
        target_change_ids = [change_id]
    elif all_changes:
        if changes_dir.is_dir():
            target_change_ids = sorted(
                p.name for p in changes_dir.iterdir() if p.is_dir() and (p / "change.md").is_file()
            )
    else:
        active = resolve_active_change_id(project_root)
        if active:
            target_change_ids = [active]
        else:
            report = ChangeCIReport(
                change_id="(unresolved)",
                status="FAIL",
                intent="",
                baseline_commit=None,
                verified_commit=None,
                error_message=(
                    "No active change contract could be unambiguously resolved from Git branch or workspace. "
                    "Specify target change with 'goatee ci <change-id>' or pass '--all'."
                ),
            )
            res = CIRunResult(
                overall_status="FAIL",
                exit_code=1,
                allow_needs_review=allow_needs_review,
                reports=[report],
                project_root=project_root,
            )
            if write_step_summary:
                write_github_step_summary(format_github_step_summary(res))
            return res

    if not target_change_ids:
        report = ChangeCIReport(
            change_id="(none)",
            status="FAIL",
            intent="",
            baseline_commit=None,
            verified_commit=None,
            error_message="No active or unarchived changes found in .goatee/changes/ to verify.",
        )
        res = CIRunResult(
            overall_status="FAIL",
            exit_code=1,
            allow_needs_review=allow_needs_review,
            reports=[report],
            project_root=project_root,
        )
        if write_step_summary:
            write_github_step_summary(format_github_step_summary(res))
        return res

    reports: List[ChangeCIReport] = []
    has_fail = False
    has_needs_review = False

    template_path = Path(__file__).parent.parent / "templates" / "receipt.md"

    for cid in target_change_ids:
        contract_path = changes_dir / cid / "change.md"
        if not contract_path.is_file():
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent="",
                    baseline_commit=None,
                    verified_commit=None,
                    error_message=f"Change contract not found at {contract_path}",
                )
            )
            has_fail = True
            continue

        # 1. Lint change contract
        lint_issues = lint_change_contract(contract_path)
        lint_errors = [i for i in lint_issues if i.severity == "ERROR"]

        # 2. Parse contract
        try:
            contract = parse_change_contract(contract_path)
        except ContractSyntaxError as cse:
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent="",
                    baseline_commit=None,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message=f"Contract syntax error: {cse}",
                )
            )
            has_fail = True
            continue

        # 3. Check approvals and baseline commit
        if contract.contract_state.agreement_status != "APPROVED":
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent=contract.intent,
                    baseline_commit=contract.contract_state.baseline_commit,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message=f"Agreement-Status is '{contract.contract_state.agreement_status}', must be 'APPROVED'.",
                )
            )
            has_fail = True
            continue

        if contract.contract_state.budget_status != "APPROVED":
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent=contract.intent,
                    baseline_commit=contract.contract_state.baseline_commit,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message=f"Budget-Status is '{contract.contract_state.budget_status}', must be 'APPROVED'.",
                )
            )
            has_fail = True
            continue

        if not contract.contract_state.baseline_commit:
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent=contract.intent,
                    baseline_commit=None,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message="Change has no Baseline-Commit recorded. Contract must be approved before CI.",
                )
            )
            has_fail = True
            continue

        # 4. Check contract tamper integrity
        locked_hash = read_contract_hash(contract_path)
        if locked_hash:
            current_hash = compute_contract_hash(contract_path.read_text(encoding="utf-8"))
            if current_hash != locked_hash:
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=None,
                        lint_issues=lint_issues,
                        error_message=(
                            f"Contract integrity violation: Approved hash {locked_hash[:8]} "
                            f"does not match current hash {current_hash[:8]}."
                        ),
                    )
                )
                has_fail = True
                continue

        # If lint errors exist, halt before verify
        if lint_errors:
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent=contract.intent,
                    baseline_commit=contract.contract_state.baseline_commit,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message=f"Contract linting failed with {len(lint_errors)} error(s).",
                )
            )
            has_fail = True
            continue

        # 5. Execute verification engine
        try:
            verif_res = verify_change(contract, project_root)
        except GitInspectionError as gie:
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent=contract.intent,
                    baseline_commit=contract.contract_state.baseline_commit,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message=f"Git inspection error: {gie}",
                )
            )
            has_fail = True
            continue
        except Exception as e:
            reports.append(
                ChangeCIReport(
                    change_id=cid,
                    status="FAIL",
                    intent=contract.intent,
                    baseline_commit=contract.contract_state.baseline_commit,
                    verified_commit=None,
                    lint_issues=lint_issues,
                    error_message=f"Verification failure: {e}",
                )
            )
            has_fail = True
            continue

        # Write delivery receipt only if explicitly requested and not already attested
        receipt_file = changes_dir / cid / "receipt.md"
        review_file = changes_dir / cid / "review.md"
        if write_receipt and not review_file.is_file():
            receipt_content = render_receipt_markdown(verif_res, template_path)
            receipt_file.write_text(receipt_content, encoding="utf-8")

        # 6. Read human review if present and validate its integrity against receipt
        human_verdict = None

        if review_file.is_file():
            if not receipt_file.is_file():
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=verif_res.verified_commit,
                        lint_issues=lint_issues,
                        verification_result=verif_res,
                        human_verdict=None,
                        error_message="Review integrity violation: review.md exists but receipt.md is missing.",
                    )
                )
                has_fail = True
                continue

            try:
                receipt = parse_receipt(receipt_file)
            except ReceiptSyntaxError as rse:
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=verif_res.verified_commit,
                        lint_issues=lint_issues,
                        verification_result=verif_res,
                        human_verdict=None,
                        error_message=f"Delivery receipt syntax error: {rse}",
                    )
                )
                has_fail = True
                continue

            # Check application drift between verified commit in receipt and current HEAD
            res_diff = subprocess.run(
                ["git", "diff", "--name-only", receipt.verified_commit, "HEAD"],
                capture_output=True,
                text=True,
                cwd=project_root
            )
            if res_diff.returncode != 0:
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=verif_res.verified_commit,
                        lint_issues=lint_issues,
                        verification_result=verif_res,
                        human_verdict=None,
                        error_message=f"Git inspection error during CI drift check: {res_diff.stderr.strip()}",
                    )
                )
                has_fail = True
                continue

            app_diffs = [
                f.strip() for f in res_diff.stdout.splitlines()
                if f.strip() and not is_control_artifact(f.strip())
            ]
            if app_diffs:
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=verif_res.verified_commit,
                        lint_issues=lint_issues,
                        verification_result=verif_res,
                        human_verdict=None,
                        error_message=(
                            f"Implementation was modified after verification at commit {receipt.verified_commit[:8]} "
                            f"(modified files: {', '.join(app_diffs[:3])})."
                        ),
                    )
                )
                has_fail = True
                continue

            # Replay and validate full review integrity using load_and_validate_review
            try:
                review_record = load_and_validate_review(
                    changes_dir / cid, contract, receipt, project_root
                )
                if review_record:
                    human_verdict = review_record.verdict
                else:
                    human_verdict = None
            except ControlStateError as cse:
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=verif_res.verified_commit,
                        lint_issues=lint_issues,
                        verification_result=verif_res,
                        human_verdict=None,
                        error_message=f"Review integrity violation: {cse}",
                    )
                )
                has_fail = True
                continue
            except Exception as e:
                reports.append(
                    ChangeCIReport(
                        change_id=cid,
                        status="FAIL",
                        intent=contract.intent,
                        baseline_commit=contract.contract_state.baseline_commit,
                        verified_commit=verif_res.verified_commit,
                        lint_issues=lint_issues,
                        verification_result=verif_res,
                        human_verdict=None,
                        error_message=f"Failed to validate human review: {e}",
                    )
                )
                has_fail = True
                continue

        # Invariant: Human acceptance must NEVER override failed machine evidence
        change_status = verif_res.verdict
        if human_verdict == "REJECTED":
            change_status = "FAIL"
        elif verif_res.verdict in ("PASS", "NEEDS_REVIEW") and human_verdict == "ACCEPTED":
            # If all automated checks passed (NEEDS_REVIEW) and human verified all manual criteria, it counts as PASS
            change_status = "PASS"
        elif verif_res.verdict == "FAIL":
            change_status = "FAIL"

        if change_status == "FAIL":
            has_fail = True
        elif change_status == "NEEDS_REVIEW":
            has_needs_review = True

        reports.append(
            ChangeCIReport(
                change_id=cid,
                status=change_status,
                intent=contract.intent,
                baseline_commit=contract.contract_state.baseline_commit,
                verified_commit=verif_res.verified_commit,
                lint_issues=lint_issues,
                verification_result=verif_res,
                human_verdict=human_verdict,
            )
        )

    # Calculate overall status and exit code
    if has_fail:
        overall_status = "FAIL"
        exit_code = 1
    elif has_needs_review:
        if allow_needs_review:
            overall_status = "NEEDS_REVIEW_ALLOWED"
            exit_code = 0
        else:
            overall_status = "NEEDS_REVIEW"
            exit_code = 1
    else:
        overall_status = "PASS"
        exit_code = 0

    run_result = CIRunResult(
        overall_status=overall_status,
        exit_code=exit_code,
        allow_needs_review=allow_needs_review,
        reports=reports,
        project_root=project_root,
    )

    if write_step_summary:
        summary_md = format_github_step_summary(run_result)
        write_github_step_summary(summary_md)

    return run_result


def format_github_step_summary(result: CIRunResult) -> str:
    """Format GitHub Actions Step Summary Markdown."""
    lines: List[str] = []

    badge = {
        "PASS": "🟢 **PASSED**",
        "NEEDS_REVIEW_ALLOWED": "🟡 **NEEDS REVIEW (ALLOWED)**",
        "NEEDS_REVIEW": "🟡 **NEEDS REVIEW (BLOCKED)**",
        "FAIL": "🔴 **FAILED**",
    }.get(result.overall_status, result.overall_status)

    lines.append(f"# GoATee Delivery &amp; Governance CI Summary: {badge}\n")

    for report in result.reports:
        rep_badge = {
            "PASS": "✅ PASS",
            "NEEDS_REVIEW": "⚠️ NEEDS_REVIEW",
            "FAIL": "❌ FAIL",
        }.get(report.status, report.status)

        lines.append(f"## Change: `{report.change_id}` ({rep_badge})\n")
        if report.intent:
            lines.append(f"> **Intent**: {report.intent}\n")

        if report.error_message:
            lines.append(f"> [!CAUTION]\n> **Error**: {report.error_message}\n")

        if report.baseline_commit or report.verified_commit:
            lines.append(
                f"- **Baseline Commit**: `{report.baseline_commit or 'none'}`\n"
                f"- **Verified Commit**: `{report.verified_commit or 'none'}`\n"
            )

        if report.verification_result:
            vr = report.verification_result
            lines.append("### Requirement Coverage Matrix\n")
            lines.append("| Criterion | Type | Declared Verification | Observed Result | Status |")
            lines.append("| :--- | :---: | :--- | :--- | :---: |")
            for row in vr.criterion_results:
                status_badge = {
                    "VERIFIED": "🟢 VERIFIED",
                    "FAILED": "🔴 FAILED",
                    "NEEDS_REVIEW": "🟡 NEEDS_REVIEW",
                    "NOT_RUN": "⚪ NOT_RUN",
                }.get(row.status, row.status)
                lines.append(
                    f"| **{row.criterion_id}** | `{row.evidence_type}` | `{row.declared}` | {row.observed} | **{status_badge}** |"
                )
            lines.append("")

            lines.append("### Build Budget &amp; Surface Analysis\n")
            lines.append(f"- **Expected Surface Changes**: {len(vr.budget_result.expected_changes)}")
            for fpath in vr.budget_result.expected_changes:
                lines.append(f"  - `{fpath}`")

            if vr.budget_result.surface_deviations:
                lines.append(f"- **Surface Deviations (UNAPPROVED)**: {len(vr.budget_result.surface_deviations)}")
                for fpath in vr.budget_result.surface_deviations:
                    lines.append(f"  - ⚠️ `{fpath}`")
            else:
                lines.append("- **Surface Deviations**: *(none)*")

            if vr.budget_result.protected_changes:
                lines.append(f"- **Protected Changes (BLAST RADIUS VIOLATION)**: {len(vr.budget_result.protected_changes)}")
                for fpath in vr.budget_result.protected_changes:
                    lines.append(f"  - 🚨 `{fpath}`")
            else:
                lines.append("- **Protected Changes**: *(none)*")

            lines.append("")
            if vr.mutations:
                lines.append("> [!WARNING]\n> **Git Mutations Detected During Verification**:")
                for mut in vr.mutations:
                    lines.append(f"> - `{mut}`")
                lines.append("")

        if report.lint_issues:
            lines.append("### Contract Linter Diagnostics\n")
            lines.append("| Severity | Section | Message |")
            lines.append("| :---: | :--- | :--- |")
            for issue in report.lint_issues:
                sev_icon = "🔴 ERROR" if issue.severity == "ERROR" else "🟡 WARNING"
                lines.append(f"| {sev_icon} | {issue.section} | {issue.message} |")
            lines.append("")

        lines.append("---\n")

    lines.append(
        "*Generated automatically by GoATee CI &amp; Specification Control Layer. Zero external runtime dependencies.*"
    )
    return "\n".join(lines)


def write_github_step_summary(content: str) -> Optional[str]:
    """Append content to GITHUB_STEP_SUMMARY environment file if defined."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return None
    try:
        p = Path(summary_path)
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n" + content + "\n")
        return summary_path
    except Exception as e:
        print(f"Warning: Failed to write to GITHUB_STEP_SUMMARY ({summary_path}): {e}", file=sys.stderr)
        return None


def ci_result_to_dict(result: CIRunResult) -> Dict[str, Any]:
    """Convert CIRunResult to JSON-serializable dictionary."""
    reports_data = []
    for r in result.reports:
        rep_dict: Dict[str, Any] = {
            "change_id": r.change_id,
            "status": r.status,
            "intent": r.intent,
            "baseline_commit": r.baseline_commit,
            "verified_commit": r.verified_commit,
            "human_verdict": r.human_verdict,
            "error_message": r.error_message,
            "lint_issues": [
                {"severity": i.severity, "section": i.section, "message": i.message}
                for i in r.lint_issues
            ],
        }
        if r.verification_result:
            vr = r.verification_result
            rep_dict["verification"] = {
                "verdict": vr.verdict,
                "verified_at": vr.verified_at,
                "matrix": [
                    {
                        "criterion_id": m.criterion_id,
                        "criterion_type": m.evidence_type,
                        "declared": m.declared,
                        "observed": m.observed,
                        "status": m.status,
                    }
                    for m in vr.criterion_results
                ],
                "expected_changes": vr.budget_result.expected_changes,
                "surface_deviations": vr.budget_result.surface_deviations,
                "protected_changes": vr.budget_result.protected_changes,
                "mutations": vr.mutations,
            }
        reports_data.append(rep_dict)

    return {
        "overall_status": result.overall_status,
        "exit_code": result.exit_code,
        "allow_needs_review": result.allow_needs_review,
        "reports": reports_data,
    }
