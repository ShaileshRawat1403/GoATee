"""Deterministic verification and delivery receipt engine for GoATee v0.1 Phase 3."""

import hashlib
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from goatee.core.budget import compare_budget, get_git_changed_files, BudgetComparisonResult, GitInspectionError
from goatee.core.models import AcceptanceCriterion, ChangeContract


UNSUPPORTED_SHELL_OPERATORS = ["|", "&&", "||", ";", ">", ">>", "<", "&"]
DEFAULT_VERIFICATION_TIMEOUT = 120.0


@dataclass
class CriterionResult:
    criterion_id: str
    evidence_type: str
    declared: str
    observed: str
    status: str  # VERIFIED | FAILED | NEEDS_REVIEW | NOT_RUN
    diagnostic: str = ""


@dataclass
class VerificationResult:
    change_id: str
    baseline_commit: str
    verified_commit: str
    budget_result: BudgetComparisonResult
    criterion_results: List[CriterionResult]
    mutations: List[str]
    verdict: str  # PASS | NEEDS_REVIEW
    verified_at: str


def resolve_head(cwd: Optional[Path] = None) -> str:
    """Resolve current HEAD commit SHA.

    Raises GitInspectionError if resolution fails.
    """
    res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        raise GitInspectionError(f"Unable to resolve HEAD: {res.stderr.strip()}")
    head = res.stdout.strip()
    if not head:
        raise GitInspectionError("Unable to resolve HEAD: Git returned empty commit SHA.")
    return head


def get_git_visible_fingerprint(cwd: Optional[Path] = None) -> Dict[str, str]:
    """Capture a fingerprint of all Git-visible files (status code + content hash).
    
    Includes both tracked modified/staged files and untracked files, INCLUDING .goatee/**.
    Format: dict mapping relative path -> hash representation (or status code for deletions).
    Raises GitInspectionError if git status fails.
    """
    res = subprocess.run(["git", "status", "--porcelain=v1"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        raise GitInspectionError(f"git status failed: {res.stderr.strip()}")

    fingerprint: Dict[str, str] = {}
    base_dir = cwd or Path.cwd()

    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        status_part = line[:2]
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ")[1].strip()
        from goatee.core.budget import unquote_git_path
        path_part = unquote_git_path(path_part)

        target_file = base_dir / path_part
        if target_file.is_file():
            try:
                content_hash = hashlib.sha256(target_file.read_bytes()).hexdigest()[:16]
                fingerprint[path_part] = f"{status_part}:{content_hash}"
            except Exception:
                fingerprint[path_part] = f"{status_part}:read_err"
        else:
            fingerprint[path_part] = f"{status_part}:absent"

    return fingerprint


def detect_mutations(
    before_fp: Dict[str, str],
    after_fp: Dict[str, str]
) -> List[str]:
    """Detect changes between two Git-visible state fingerprints."""
    mutations: List[str] = []
    all_keys = sorted(set(before_fp.keys()) | set(after_fp.keys()))
    for k in all_keys:
        val_before = before_fp.get(k)
        val_after = after_fp.get(k)
        if val_before != val_after:
            if val_before is None:
                mutations.append(f"Created/untracked: {k}")
            elif val_after is None:
                mutations.append(f"Reverted/deleted: {k}")
            else:
                mutations.append(f"Modified: {k}")
    return mutations


def validate_evidence_declaration(ac: AcceptanceCriterion) -> Optional[int]:
    """Validate declared automated evidence command and expected string.
    
    Returns expected exit code as integer, or raises ValueError.
    """
    if ac.evidence_type != "automated":
        return None

    if not ac.command or not ac.command.strip():
        raise ValueError(f"Criterion {ac.id} is automated but has no command declared.")

    if not ac.expected or not ac.expected.strip():
        raise ValueError(f"Criterion {ac.id} is automated but has no expected result declared.")

    # Validate narrow exit N grammar
    m = re.match(r"^exit\s+(\d+)$", ac.expected.strip(), re.IGNORECASE)
    if not m:
        raise ValueError(
            f"Criterion {ac.id} has invalid expected format '{ac.expected}'. "
            "Phase 3 requires 'exit N' (e.g. 'exit 0')."
        )
    expected_code = int(m.group(1))

    # Validate command does not use unsupported shell operators
    for op in UNSUPPORTED_SHELL_OPERATORS:
        # Match standalone operator or bounded by whitespace
        if f" {op} " in f" {ac.command} ":
            raise ValueError(
                f"Criterion {ac.id} command contains unsupported shell operator '{op}'. "
                "Phase 3 executes commands directly without shell pipelines."
            )

    return expected_code


def execute_criterion_command(
    command_str: str,
    cwd: Optional[Path] = None,
    timeout_seconds: Optional[float] = None
) -> Tuple[Optional[int], float, str, str, bool]:
    """Execute command directly using shlex.split, shell=False, stdin=DEVNULL, and bounded timeout.

    Returns (returncode, duration, stdout, stderr, is_timeout).
    """
    effective_timeout = timeout_seconds if timeout_seconds is not None else DEFAULT_VERIFICATION_TIMEOUT
    args = shlex.split(command_str)
    start_time = time.time()
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            timeout=effective_timeout
        )
        duration = time.time() - start_time
        return res.returncode, duration, res.stdout, res.stderr, False
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start_time
        stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode("utf-8", errors="replace") if e.stdout else "")
        stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode("utf-8", errors="replace") if e.stderr else "")
        return None, duration, stdout, stderr, True


def bounded_excerpt(stdout: str, stderr: str, max_lines: int = 15, max_bytes: int = 2048) -> str:
    """Extract bounded diagnostic snippet from stdout and stderr with line and byte limits."""
    combined = []
    if stdout.strip():
        combined.extend(stdout.splitlines())
    if stderr.strip():
        combined.extend(stderr.splitlines())
    
    if not combined:
        return "(no output)"
    
    if len(combined) <= max_lines:
        text = "\n".join(combined)
    else:
        if max_lines <= 1:
            combined = ["..."]
        else:
            combined = ["..."] + combined[-(max_lines - 1):]
        text = "\n".join(combined)
    
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        notice = f"\n... [truncated to {max_bytes} bytes; truncation does not guarantee secret removal]"
        notice_bytes = len(notice.encode("utf-8"))
        if max_bytes > notice_bytes:
            sliced = encoded[:max_bytes - notice_bytes].decode("utf-8", errors="ignore")
            sliced_lines = sliced.splitlines()
            notice_line_count = len(notice.strip().splitlines())
            allowed_sliced_lines = max_lines - notice_line_count
            if len(sliced_lines) > allowed_sliced_lines:
                if allowed_sliced_lines > 1:
                    sliced_lines = ["..."] + sliced_lines[-(allowed_sliced_lines - 1):]
                elif allowed_sliced_lines == 1:
                    sliced_lines = ["..."]
                else:
                    sliced_lines = []
            text = "\n".join(sliced_lines) + notice
        else:
            text = encoded[:max_bytes].decode("utf-8", errors="ignore")
    
    return text


def verify_change(
    contract: ChangeContract,
    repo_root: Path
) -> VerificationResult:
    """Run deterministic verification pipeline against committed candidate HEAD."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    baseline = contract.contract_state.baseline_commit or ""
    
    # 1. Capture Verified-Commit = HEAD (fails closed on Git error)
    verified_commit = resolve_head(repo_root)

    # 2. Budget Gate: compare baseline..HEAD (fails closed on Git error)
    changed_files = get_git_changed_files(baseline, "HEAD", cwd=repo_root)
    budget = contract.build_budget
    budget_result = compare_budget(changed_files, budget) if budget else BudgetComparisonResult()

    criterion_results: List[CriterionResult] = []
    all_mutations: List[str] = []

    has_budget_deviation = bool(budget_result.surface_deviations or budget_result.protected_changes)

    # If budget deviation exists, gate execution: DO NOT run any commands
    if has_budget_deviation:
        for ac in contract.acceptance_criteria:
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type=ac.evidence_type,
                declared=ac.command if ac.evidence_type == "automated" else (ac.instruction or ""),
                observed="Blocked by unapproved budget deviations",
                status="NOT_RUN",
                diagnostic="Execution halted: Build Budget contains surface deviations or protected changes."
            ))
        return VerificationResult(
            change_id=contract.change_id,
            baseline_commit=baseline,
            verified_commit=verified_commit,
            budget_result=budget_result,
            criterion_results=criterion_results,
            mutations=[],
            verdict="FAIL",
            verified_at=now_str
        )

    # 3. Validate Evidence Declarations upfront
    expected_exit_codes: Dict[str, Optional[int]] = {}
    for ac in contract.acceptance_criteria:
        expected_exit_codes[ac.id] = validate_evidence_declaration(ac)

    # 4. Execute Automated Criteria loop
    halted_due_to_mutation = False

    for ac in contract.acceptance_criteria:
        if ac.evidence_type == "manual":
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="manual",
                declared=ac.instruction or "(no instruction)",
                observed="Pending human observation and sign-off",
                status="NEEDS_REVIEW"
            ))
            continue

        if halted_due_to_mutation:
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{ac.command} ({ac.expected})",
                observed="Not run due to previous repository mutation",
                status="NOT_RUN"
            ))
            continue

        expected_code = expected_exit_codes[ac.id]
        cmd = ac.command or ""

        # Pre-execution invariant checks
        try:
            head_before = resolve_head(repo_root)
        except GitInspectionError as gie:
            halted_due_to_mutation = True
            all_mutations.append(f"Git inspection failed before criterion: {gie}")
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed="GIT INSPECTION FAILED",
                status="FAILED",
                diagnostic=f"GIT INSPECTION FAILED: {gie}"
            ))
            continue

        if head_before != verified_commit:
            halted_due_to_mutation = True
            all_mutations.append(f"HEAD mutated before criterion from '{verified_commit}' to '{head_before}'")
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed=f"HEAD MUTATION DETECTED [HEAD={head_before[:7]}]",
                status="FAILED",
                diagnostic=f"HEAD MUTATION DETECTED: HEAD was '{head_before}', expected '{verified_commit}'."
            ))
            continue

        try:
            fp_before = get_git_visible_fingerprint(repo_root)
        except GitInspectionError as gie:
            halted_due_to_mutation = True
            all_mutations.append(f"Git inspection failed before criterion: {gie}")
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed="GIT INSPECTION FAILED",
                status="FAILED",
                diagnostic=f"GIT INSPECTION FAILED: {gie}"
            ))
            continue

        # Execute command
        actual_code, duration, stdout, stderr, is_timeout = execute_criterion_command(cmd, cwd=repo_root)

        # Post-execution invariant checks
        try:
            head_after = resolve_head(repo_root)
        except GitInspectionError as gie:
            halted_due_to_mutation = True
            all_mutations.append(f"Git inspection failed after criterion: {gie}")
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed="GIT INSPECTION FAILED",
                status="FAILED",
                diagnostic=f"GIT INSPECTION FAILED: {gie}"
            ))
            continue

        if head_after != verified_commit:
            halted_due_to_mutation = True
            all_mutations.append(f"HEAD mutated after criterion from '{verified_commit}' to '{head_after}'")
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed=f"HEAD MUTATION DETECTED [HEAD={head_after[:7]}]",
                status="FAILED",
                diagnostic=f"HEAD MUTATION DETECTED: Command modified repository HEAD from '{verified_commit}' to '{head_after}'."
            ))
            continue

        try:
            fp_after = get_git_visible_fingerprint(repo_root)
        except GitInspectionError as gie:
            halted_due_to_mutation = True
            all_mutations.append(f"Git inspection failed after criterion: {gie}")
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed="GIT INSPECTION FAILED",
                status="FAILED",
                diagnostic=f"GIT INSPECTION FAILED: {gie}"
            ))
            continue

        # Check mutations
        mutations = detect_mutations(fp_before, fp_after)
        if mutations:
            all_mutations.extend(mutations)
            halted_due_to_mutation = True
            diagnostic = f"MUTATION DETECTED: {', '.join(mutations)}\n" + bounded_excerpt(stdout, stderr)
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed=f"exit {actual_code} ({duration:.2f}s) [MUTATED REPO]",
                status="FAILED",
                diagnostic=diagnostic
            ))
            continue

        # Check timeout
        if is_timeout:
            diag = f"TIMEOUT: Command exceeded {duration:.1f}s execution limit.\n" + bounded_excerpt(stdout, stderr)
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed=f"TIMEOUT after {int(duration)}s",
                status="FAILED",
                diagnostic=diag
            ))
            continue

        # Evaluate exit code match
        if actual_code == expected_code:
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed=f"exit {actual_code} ({duration:.2f}s)",
                status="VERIFIED"
            ))
        else:
            diag = bounded_excerpt(stdout, stderr)
            criterion_results.append(CriterionResult(
                criterion_id=ac.id,
                evidence_type="automated",
                declared=f"{cmd} ({ac.expected})",
                observed=f"exit {actual_code} ({duration:.2f}s)",
                status="FAILED",
                diagnostic=diag
            ))

    # 5. Evaluate Overall Delivery Verdict
    # Non-negotiable invariant: any machine failure (test failure, timeout, budget deviation,
    # or Git mutation) MUST yield verdict = "FAIL". Human acceptance can only resolve pending
    # manual criteria ("NEEDS_REVIEW") when all machine evidence is clean.
    has_failed_criterion = any(r.status in ("FAILED", "REJECTED") for r in criterion_results)
    has_machine_failure = has_failed_criterion or has_budget_deviation or bool(all_mutations)

    if has_machine_failure:
        verdict = "FAIL"
    elif any(r.status == "NEEDS_REVIEW" for r in criterion_results):
        verdict = "NEEDS_REVIEW"
    elif all(r.status == "VERIFIED" for r in criterion_results):
        verdict = "PASS"
    else:
        verdict = "NEEDS_REVIEW"

    return VerificationResult(
        change_id=contract.change_id,
        baseline_commit=baseline,
        verified_commit=verified_commit,
        budget_result=budget_result,
        criterion_results=criterion_results,
        mutations=all_mutations,
        verdict=verdict,
        verified_at=now_str
    )


def render_receipt_markdown(result: VerificationResult, template_path: Path) -> str:
    """Render structured VerificationResult into delivery receipt markdown."""
    template_text = template_path.read_text(encoding="utf-8")

    # Coverage matrix lines
    matrix_lines = []
    for r in result.criterion_results:
        decl_clean = r.declared.replace("|", "\\|")
        obs_clean = r.observed.replace("|", "\\|")
        matrix_lines.append(
            f"| **{r.criterion_id}** | {r.evidence_type} | `{decl_clean}` | {obs_clean} | **{r.status}** |"
        )
    coverage_matrix = "\n".join(matrix_lines) or "| (none) | | | | |"

    # Budget sections
    exp_lines = "\n".join(f"- {s}" for s in result.budget_result.expected_changes) or "- (none)"
    dev_lines = "\n".join(f"- {d}" for d in result.budget_result.surface_deviations) or "- (none)"
    prot_lines = "\n".join(f"- {p}" for p in result.budget_result.protected_changes) or "- (none)"

    # Mutation report
    if result.mutations:
        mut_report = "### Git-Visible Mutations Detected\n" + "\n".join(f"- {m}" for m in result.mutations)
    else:
        mut_report = "- Zero Git-visible repository mutations detected during verification."

    # Diagnostics
    diag_blocks = []
    for r in result.criterion_results:
        if r.diagnostic:
            diag_blocks.append(f"### {r.criterion_id} Diagnostic\n```text\n{r.diagnostic}\n```")
    diagnostics = "\n\n".join(diag_blocks) if diag_blocks else "- No diagnostics recorded (all executed checks clean)."

    return template_text.format(
        change_id=result.change_id,
        verdict=result.verdict,
        verified_at=result.verified_at,
        baseline_commit=result.baseline_commit,
        verified_commit=result.verified_commit,
        coverage_matrix=coverage_matrix,
        expected_changes=exp_lines,
        surface_deviations=dev_lines,
        protected_changes=prot_lines,
        mutation_report=mut_report,
        diagnostic_excerpt=diagnostics
    )


def format_failure_diagnostic(
    result: VerificationResult,
    show_details: bool = False,
    max_lines: int = 15,
    max_bytes: int = 2048,
) -> str:
    """Format actionable terminal failure metadata, gating detailed excerpts behind show_details."""
    if result.verdict != "FAIL":
        return ""

    lines = ["\n--- Verification Failure Details ---"]

    # 1. Build Budget deviations
    if result.budget_result.surface_deviations or result.budget_result.protected_changes:
        lines.append("Build Budget Deviations:")
        if result.budget_result.surface_deviations:
            lines.append(f"  Surface Deviations ({len(result.budget_result.surface_deviations)}):")
            for d in result.budget_result.surface_deviations:
                lines.append(f"    - {d}")
        if result.budget_result.protected_changes:
            lines.append(f"  Protected Changes ({len(result.budget_result.protected_changes)}):")
            for p in result.budget_result.protected_changes:
                lines.append(f"    - {p}")
        lines.append("  Action: Run 'goatee check' to inspect working tree; if intentional, use 'goatee amend --add-surface <path> --note <note>'.")

    # 2. Repository mutations
    if result.mutations:
        lines.append(f"Repository Mutations Detected ({len(result.mutations)}):")
        for m in result.mutations:
            lines.append(f"  - {m}")
        lines.append("  Action: Ensure test commands do not write untracked files or mutate repository HEAD.")

    # 3. Failing criteria
    failing_rows = [r for r in result.criterion_results if r.status in ("FAILED", "NOT_RUN")]
    if failing_rows:
        lines.append(f"Failing Criteria ({len(failing_rows)}):")
        has_diagnostics = False
        for r in failing_rows:
            lines.append(f"  - [{r.status}] {r.criterion_id} ({r.evidence_type})")
            lines.append(f"    Declared: {r.declared}")
            lines.append(f"    Observed: {r.observed}")
            if r.diagnostic:
                has_diagnostics = True
                if show_details:
                    excerpt = bounded_excerpt(r.diagnostic, "", max_lines=max_lines, max_bytes=max_bytes)
                    lines.append("    Diagnostic Excerpt:")
                    for dl in excerpt.splitlines():
                        lines.append(f"      | {dl}")

        if not show_details and has_diagnostics:
            lines.append("\nNotice: Detailed command output excerpts hidden by default to protect sensitive data.")
            lines.append("Re-run with 'goatee verify --diagnostics' (or -d) to view bounded command output excerpts.")

    lines.append("Action: Inspect receipt in .goatee/changes/<change_id>/receipt.md, fix implementation, and re-run 'goatee verify'.")
    lines.append("------------------------------------")
    return "\n".join(lines)
