"""Unit and integration tests for formal budget amendment workflow."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from goatee.core.parser import parse_change_contract, read_contract_hash, compute_contract_hash
from goatee.cli import build_parser, cmd_approve, cmd_amend, cmd_verify


class TestBudgetAmendment(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Lead Reviewer"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "lead@example.com"], cwd=self.repo_root, check=True)

        (self.repo_root / "README.md").write_text("# Test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_root, check=True, capture_output=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text("Project-Name: TestProj\n", encoding="utf-8")

        change_dir = goatee_dir / "changes" / "001-amend"
        change_dir.mkdir(parents=True)
        self.change_dir = change_dir
        self.change_file = change_dir / "change.md"

        contract_text = f"""# Change: 001-amend

## Intent
Test budget amendment.

## Agreed Scope
### In Scope
- Work

### Out of Scope
- None

## Acceptance Criteria
- AC-01: Auto check
  Evidence:
    type: automated
    command: python3 -c "print('ok')"
    expected: exit 0

## Build Budget
### Expected Surface
- src/app.py

### Reuse Strategy
- None

Dependency-Policy: No new runtime dependencies

### Blast Radius
- None

## Contract State
Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: 
"""
        self.change_file.write_text(contract_text, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_amend_adds_surface_and_relocks_hash(self):
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.repo_root)
            parser = build_parser()

            # 1. Approve initial contract
            cmd_approve(parser.parse_args(["approve", "001-amend"]))
            initial_hash = read_contract_hash(self.change_file)
            self.assertIsNotNone(initial_hash)

            # 2. Amend contract to add extra surface
            args = parser.parse_args([
                "amend", "001-amend",
                "--add-surface", "src/helper.py",
                "--note", "Needed helper for math utilities"
            ])
            ret = cmd_amend(args)
            self.assertEqual(ret, 0)

            # 3. Check updated change.md
            content = self.change_file.read_text(encoding="utf-8")
            self.assertIn("- src/helper.py", content)
            self.assertIn("## Amendments", content)
            self.assertIn("Needed helper for math utilities", content)
            self.assertIn("Lead Reviewer", content)

            new_hash = read_contract_hash(self.change_file)
            self.assertIsNotNone(new_hash)
            self.assertNotEqual(initial_hash, new_hash)
            self.assertEqual(new_hash, compute_contract_hash(content))

            # 4. Verify that verification passes with the amended file
            src_dir = self.repo_root / "src"
            src_dir.mkdir()
            (src_dir / "app.py").write_text("# app\n", encoding="utf-8")
            (src_dir / "helper.py").write_text("# helper\n", encoding="utf-8")

            subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "feat: amended delivery"], cwd=self.repo_root, check=True, capture_output=True)

            ret_v = cmd_verify(parser.parse_args(["verify", "001-amend"]))
            self.assertEqual(ret_v, 0)
        finally:
            os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
