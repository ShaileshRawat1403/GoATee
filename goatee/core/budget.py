"""Deterministic Build Budget comparison for GoATee v0.1 Phase 2."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import subprocess
from goatee.core.models import BuildBudget


@dataclass(frozen=True)
class BudgetComparisonResult:
    expected_changes: List[str] = field(default_factory=list)
    surface_deviations: List[str] = field(default_factory=list)
    protected_changes: List[str] = field(default_factory=list)
    ignored_control_files: List[str] = field(default_factory=list)


def normalize_relpath(path_str: str) -> str:
    """Normalize a path string into a clean relative POSIX path without leading slashes."""
    p = path_str.strip().replace("\\", "/")
    while p.startswith("/"):
        p = p[1:]
    return p


def is_control_artifact(path_str: str) -> bool:
    """Check if a path belongs to GoATee's own .goatee/** control plane."""
    norm = normalize_relpath(path_str)
    return norm == ".goatee" or norm.startswith(".goatee/")


def matches_path_entry(file_path: str, entry: str) -> bool:
    """Check if file_path matches entry.
    
    Supports:
    - Exact file matches: 'goatee/cli.py' matches 'goatee/cli.py'
    - Directory prefixes: 'tests/' or 'tests' matches 'tests/test_x.py' and 'tests/sub/test_y.py'
    No glob patterns.
    """
    file_norm = normalize_relpath(file_path)
    entry_norm = normalize_relpath(entry)

    if not entry_norm:
        return False

    # Ignore semantic entries (e.g. 'Phase 1 approval semantics', 'Metadata grammar')
    # Valid path entries contain file extensions or slashes or match existing directory conventions
    if " " in entry_norm and not entry_norm.startswith("./"):
        return False

    # Exact file match
    if file_norm == entry_norm:
        return True

    # Directory prefix match: ensure entry ends with '/' for clean prefix matching
    dir_prefix = entry_norm if entry_norm.endswith("/") else entry_norm + "/"
    if file_norm.startswith(dir_prefix):
        return True

    return False


def get_git_changed_files(baseline: str, target: str = "HEAD", cwd: Optional[Path] = None) -> List[str]:
    """Retrieve exact list of changed files between baseline and target using git diff --name-only."""
    res = subprocess.run(
        ["git", "diff", "--name-only", f"{baseline}..{target}"],
        capture_output=True,
        text=True,
        cwd=cwd
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to execute git diff: {res.stderr.strip()}")

    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def compare_budget(
    changed_files: List[str],
    budget: BuildBudget
) -> BudgetComparisonResult:
    """Deterministically compare changed file paths against an approved BuildBudget.
    
    Invariants:
    - .goatee/** control-plane artifacts are filtered into ignored_control_files and never flagged as deviations.
    - Path-based Blast Radius entries that are touched are reported in protected_changes.
    - Files matching Expected Surface are in expected_changes.
    - Files not matching Expected Surface are in surface_deviations.
    """
    expected: List[str] = []
    deviations: List[str] = []
    protected: List[str] = []
    ignored: List[str] = []

    for f in changed_files:
        norm_f = normalize_relpath(f)
        if not norm_f:
            continue

        # 1. Filter out control-plane artifacts
        if is_control_artifact(norm_f):
            ignored.append(norm_f)
            continue

        # 2. Check Blast Radius (protected paths)
        is_protected = False
        for br_entry in budget.blast_radius:
            if matches_path_entry(norm_f, br_entry):
                protected.append(norm_f)
                is_protected = True
                break

        # 3. Check Expected Surface
        matches_expected = False
        for exp_entry in budget.expected_surface:
            if matches_path_entry(norm_f, exp_entry):
                matches_expected = True
                break

        if matches_expected:
            expected.append(norm_f)
        else:
            deviations.append(norm_f)

    return BudgetComparisonResult(
        expected_changes=sorted(expected),
        surface_deviations=sorted(deviations),
        protected_changes=sorted(protected),
        ignored_control_files=sorted(ignored),
    )
