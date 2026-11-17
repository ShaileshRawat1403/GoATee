"""Unit and integration tests for working-tree pre-flight budget inspection."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from goatee.core.budget import (
    compare_budget,
    get_working_tree_changed_files,
    BuildBudget,
    GitChange,
)
from goatee.core.parser import parse_change_contract
from goatee.cli import cmd_check, build_parser


class TestPreFlightCheck(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Reviewer"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_root, check=True)

        # Initial commit
        (self.repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_root, check=True, capture_output=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        # Initialize GoATee project
        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text("Project-Name: TestProj\n", encoding="utf-8")

        # Create change
        change_dir = goatee_dir / "changes" / "001-feat"
        change_dir.mkdir(parents=True)
        self.change_dir = change_dir

        contract_content = f"""# Change: 001-feat

## Intent
Test intent.

## Agreed Scope
### In Scope
- Task

### Out of Scope
- None

## Acceptance Criteria
- AC-01: First test
  Evidence:
    type: automated
    command: python3 -c "print('ok')"
    expected: exit 0

## Build Budget
### Expected Surface
- src/app.py
- src/lib/

### Reuse Strategy
- None

Dependency-Policy: No new runtime dependencies

### Blast Radius
- protected/secret.py

## Contract State
Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: {self.baseline_sha}
"""
        (change_dir / "change.md").write_text(contract_content, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_working_tree_clean(self):
        changes = get_working_tree_changed_files(self.baseline_sha, cwd=self.repo_root)
        budget = BuildBudget(expected_surface=["src/app.py"])
        comp = compare_budget(changes, budget)
        self.assertEqual(len(comp.expected_changes), 0)
        self.assertEqual(len(comp.surface_deviations), 0)
        self.assertEqual(len(comp.protected_changes), 0)

    def test_working_tree_expected_untracked_and_modified(self):
        src_dir = self.repo_root / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")

        lib_dir = src_dir / "lib"
        lib_dir.mkdir()
        (lib_dir / "util.py").write_text("# util\n", encoding="utf-8")

        changes = get_working_tree_changed_files(self.baseline_sha, cwd=self.repo_root)
        budget = BuildBudget(
            expected_surface=["src/app.py", "src/lib/"],
            blast_radius=["protected/secret.py"]
        )
        comp = compare_budget(changes, budget)
        self.assertIn("src/app.py", comp.expected_changes)
        self.assertIn("src/lib/util.py", comp.expected_changes)
        self.assertEqual(len(comp.surface_deviations), 0)
        self.assertEqual(len(comp.protected_changes), 0)

    def test_working_tree_surface_deviation_and_protected_touch(self):
        # Create unapproved file outside expected surface
        (self.repo_root / "unexpected.py").write_text("# unapproved\n", encoding="utf-8")

        # Create file inside protected blast radius
        prot_dir = self.repo_root / "protected"
        prot_dir.mkdir()
        (prot_dir / "secret.py").write_text("# secret\n", encoding="utf-8")

        changes = get_working_tree_changed_files(self.baseline_sha, cwd=self.repo_root)
        budget = BuildBudget(
            expected_surface=["src/app.py"],
            blast_radius=["protected/secret.py"]
        )
        comp = compare_budget(changes, budget)
        self.assertIn("unexpected.py", comp.surface_deviations)
        self.assertIn("protected/secret.py", comp.protected_changes)

    def test_cmd_check_cli_success(self):
        src_dir = self.repo_root / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")

        orig_cwd = os.getcwd()
        try:
            os.chdir(self.repo_root)
            parser = build_parser()
            args = parser.parse_args(["check", "001-feat"])
            ret = cmd_check(args)
            self.assertEqual(ret, 0)
        finally:
            os.chdir(orig_cwd)

    def test_cmd_check_cli_failure_on_deviation(self):
        (self.repo_root / "deviant.py").write_text("# dev\n", encoding="utf-8")

        orig_cwd = os.getcwd()
        try:
            os.chdir(self.repo_root)
            parser = build_parser()
            args = parser.parse_args(["check", "001-feat"])
            ret = cmd_check(args)
            self.assertEqual(ret, 1)
        finally:
            os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
