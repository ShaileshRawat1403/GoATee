"""Deterministic Build Budget comparison for GoATee v0.1 Phase 2."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional
import subprocess
from goatee.core.models import BuildBudget


class GitInspectionError(Exception):
    """Raised when a Git inspection command fails."""
    pass


@dataclass(frozen=True, order=True)
class GitChange:
    status: str
    path: str
    old_path: Optional[str] = None


@dataclass(frozen=True)
class BudgetComparisonResult:
    expected_changes: List[str] = field(default_factory=list)
    surface_deviations: List[str] = field(default_factory=list)
    protected_changes: List[str] = field(default_factory=list)
    ignored_control_files: List[str] = field(default_factory=list)
    raw_changes: List[GitChange] = field(default_factory=list)


def unquote_git_path(path_str: str) -> str:
    """Safely strip Git C-style quotes from paths with spaces or special characters."""
    p = path_str.strip()
    if p.startswith('"') and p.endswith('"') and len(p) >= 2:
        try:
            return p[1:-1].encode("latin1").decode("unicode_escape")
        except Exception:
            return p[1:-1]
    return p


def normalize_relpath(path_str: str) -> str:
    """Normalize a path string into a clean relative POSIX path without leading slashes."""
    p = unquote_git_path(path_str).replace("\\", "/")
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
    - Directory wildcards: 'tests/**' or 'src/*' matches files under those directories
    - Glob wildcards: '*.py' or 'tests/test_*.py' matches via fnmatch
    """
    file_norm = normalize_relpath(file_path)
    entry_norm = normalize_relpath(entry)

    if not entry_norm:
        return False

    # Ignore semantic safeguard entries (e.g. 'Phase 1 approval semantics (goatee approve)')
    # Real paths contain path separators ('/'), start with './', or have file extensions.
    import re
    if " " in entry_norm and "/" not in entry_norm and not entry_norm.startswith("./") and not re.search(r"\.[a-zA-Z0-9_-]+$", entry_norm):
        return False

    # Exact file match
    if file_norm == entry_norm:
        return True

    # Strip trailing glob wildcards like /** or /* for clean directory prefix matching
    clean_entry = entry_norm
    if clean_entry.endswith("/**"):
        clean_entry = clean_entry[:-3]
    elif clean_entry.endswith("/*"):
        clean_entry = clean_entry[:-2]

    # Directory prefix match: ensure entry ends with '/' for clean prefix matching
    dir_prefix = clean_entry if clean_entry.endswith("/") else clean_entry + "/"
    if file_norm.startswith(dir_prefix):
        return True

    # Wildcard glob matching (e.g. *.md, tests/test_*.py)
    import fnmatch
    if "*" in entry_norm or "?" in entry_norm:
        if fnmatch.fnmatch(file_norm, entry_norm):
            return True

    return False


def get_git_changed_files(baseline: str, target: str = "HEAD", cwd: Optional[Path] = None) -> List[GitChange]:
    """Retrieve exact list of GitChange objects between baseline and target using git diff --name-status --find-renames."""
    res = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", f"{baseline}..{target}"],
        capture_output=True,
        text=True,
        cwd=cwd
    )
    if res.returncode != 0:
        raise GitInspectionError(f"Failed to execute git diff: {res.stderr.strip()}")

    changes: List[GitChange] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if not parts:
            continue
        status_raw = parts[0].strip()
        if status_raw.startswith("R"):
            old_p = unquote_git_path(parts[1].strip()) if len(parts) > 1 else ""
            new_p = unquote_git_path(parts[2].strip()) if len(parts) > 2 else ""
            changes.append(GitChange(status="R", path=new_p, old_path=old_p))
        elif len(parts) >= 2:
            status = status_raw[0] if status_raw else "M"
            path = unquote_git_path(parts[1].strip())
            changes.append(GitChange(status=status, path=path))
    return changes


def get_working_tree_changed_files(baseline: str, cwd: Optional[Path] = None) -> List[GitChange]:
    """Retrieve exact list of GitChange objects between baseline and the current working tree.
    
    Includes committed changes, staged and unstaged modifications, and untracked files.
    """
    res = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", baseline],
        capture_output=True,
        text=True,
        cwd=cwd
    )
    if res.returncode != 0:
        raise GitInspectionError(f"Failed to execute git diff: {res.stderr.strip()}")

    changes: List[GitChange] = []
    seen_paths = set()

    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if not parts:
            continue
        status_raw = parts[0].strip()
        if status_raw.startswith("R"):
            old_p = unquote_git_path(parts[1].strip()) if len(parts) > 1 else ""
            new_p = unquote_git_path(parts[2].strip()) if len(parts) > 2 else ""
            changes.append(GitChange(status="R", path=new_p, old_path=old_p))
            seen_paths.add(new_p)
            seen_paths.add(old_p)
        elif len(parts) >= 2:
            status = status_raw[0] if status_raw else "M"
            path = unquote_git_path(parts[1].strip())
            changes.append(GitChange(status=status, path=path))
            seen_paths.add(path)

    # Disclose untracked files from git status
    status_res = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        capture_output=True,
        text=True,
        cwd=cwd
    )
    if status_res.returncode != 0:
        raise GitInspectionError(f"Failed to execute git status: {status_res.stderr.strip()}")

    for line in status_res.stdout.splitlines():
        if not line.startswith("??"):
            continue
        raw_path = unquote_git_path(line[3:].strip())
        if raw_path and raw_path not in seen_paths:
            changes.append(GitChange(status="A", path=raw_path))
            seen_paths.add(raw_path)

    return changes


def compare_budget(
    changed_files: List[Any],
    budget: BuildBudget
) -> BudgetComparisonResult:
    """Deterministically compare changed file paths against an approved BuildBudget.
    
    Supports both List[str] and List[GitChange].
    """
    expected: List[str] = []
    deviations: List[str] = []
    protected: List[str] = []
    ignored: List[str] = []
    raw_changes: List[GitChange] = []

    for item in changed_files:
        if isinstance(item, str):
            change = GitChange(status="M", path=item)
        elif isinstance(item, GitChange):
            change = item
        else:
            continue
        raw_changes.append(change)

        norm_path = normalize_relpath(change.path)
        norm_old = normalize_relpath(change.old_path) if change.old_path else None

        if change.status == "R" and norm_old:
            old_is_ctrl = is_control_artifact(norm_old)
            new_is_ctrl = is_control_artifact(norm_path)

            if old_is_ctrl and new_is_ctrl:
                # control -> control: ignored from application delivery surface
                ignored.append(norm_path)
                continue
            elif old_is_ctrl and not new_is_ctrl:
                # control -> application: new application path evaluated against Expected Surface
                for br in budget.blast_radius:
                    if matches_path_entry(norm_path, br):
                        protected.append(norm_path)
                        break
                if any(matches_path_entry(norm_path, exp) for exp in budget.expected_surface):
                    expected.append(norm_path)
                else:
                    deviations.append(norm_path)
            elif not old_is_ctrl and new_is_ctrl:
                # application -> control: old application path must still be evaluated
                for br in budget.blast_radius:
                    if matches_path_entry(norm_old, br):
                        protected.append(norm_old)
                        break
                if any(matches_path_entry(norm_old, exp) for exp in budget.expected_surface):
                    expected.append(norm_old)
                else:
                    deviations.append(norm_old)
            else:
                # application -> application
                for p in (norm_old, norm_path):
                    for br in budget.blast_radius:
                        if matches_path_entry(p, br):
                            protected.append(p)
                            break
                if any(matches_path_entry(norm_path, exp) for exp in budget.expected_surface):
                    expected.append(norm_path)
                else:
                    deviations.append(norm_path)
        else:
            # standard non-rename (A, M, D)
            if is_control_artifact(norm_path):
                ignored.append(norm_path)
                continue

            for br in budget.blast_radius:
                if matches_path_entry(norm_path, br):
                    protected.append(norm_path)
                    break

            if any(matches_path_entry(norm_path, exp) for exp in budget.expected_surface):
                expected.append(norm_path)
            else:
                deviations.append(norm_path)

    return BudgetComparisonResult(
        expected_changes=sorted(set(expected)),
        surface_deviations=sorted(set(deviations)),
        protected_changes=sorted(set(protected)),
        ignored_control_files=sorted(set(ignored)),
        raw_changes=raw_changes,
    )
