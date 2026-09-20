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

from goatee.core.budget import compare_budget, get_git_changed_files, BudgetComparisonResult
from goatee.core.models import AcceptanceCriterion, ChangeContract


UNSUPPORTED_SHELL_OPERATORS = ["|", "&&", "||", ";", ">", ">>", "<", "&"]


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


def get_git_visible_fingerprint(cwd: Optional[Path] = None) -> Dict[str, str]:
    """Capture a fingerprint of all Git-visible files (status code + content hash).
    
    Includes both tracked modified/staged files and untracked files.
    Format: dict mapping relative path -> hash representation (or status code for deletions).
    Ignores the .goatee/ control plane.
    """
    res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        return {}

    fingerprint: Dict[str, str] = {}
    base_dir = cwd or Path.cwd()

    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        status_part = line[:2]
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ")[1].strip()

        # Exclude GoATee's own control plane
        if path_part == ".goatee" or path_part.startswith(".goatee/"):
            continue

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
    cwd: Optional[Path] = None
) -> Tuple[int, float, str, str]:
    """Execute command directly using shlex.split and shell=False."""
    args = shlex.split(command_str)
    start_time = time.time()
    res = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=cwd
    )
    duration = time.time() - start_time
    return res.returncode, duration, res.stdout, res.stderr


def bounded_excerpt(stdout: str, stderr: str, max_lines: int = 10) -> str:
    """Extract bounded diagnostic snippet from stdout and stderr."""
    combined = []
    if stdout.strip():
        combined.extend(stdout.splitlines())
    if stderr.strip():
        combined.extend(stderr.splitlines())
    
    if not combined:
        return "(no output)"
    
    if len(combined) <= max_lines:
        return "\n".join(combined)
    
    return "...\n" + "\n".join(combined[-max_lines:])


def verify_change(
    contract: ChangeContract,
    repo_root: Path
) -> VerificationResult:
    """Run deterministic verification pipeline against committed candidate HEAD."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    baseline = contract.contract_state.baseline_commit or ""
    
    # 1. Capture Verified-Commit = HEAD
    head_res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo_root)
    verified_commit = head_res.stdout.strip()

    # 2. Budget Gate: compare baseline..HEAD
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
            verdict="NEEDS_REVIEW",
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

        # Pre-execution fingerprint
        fp_before = get_git_visible_fingerprint(repo_root)

        # Execute command
        actual_code, duration, stdout, stderr = execute_criterion_command(cmd, cwd=repo_root)

        # Post-execution fingerprint
        fp_after = get_git_visible_fingerprint(repo_root)

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
    is_pass = True
    if has_budget_deviation:
        is_pass = False
    if all_mutations:
        is_pass = False
    for r in criterion_results:
        if r.status != "VERIFIED":
            is_pass = False
            break

    verdict = "PASS" if is_pass else "NEEDS_REVIEW"

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
        matrix_lines.append(
            f"| **{r.criterion_id}** | {r.evidence_type} | `{r.declared}` | {r.observed} | **{r.status}** |"
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
