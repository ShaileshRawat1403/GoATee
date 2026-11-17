"""Unit and integration tests for change contract cryptographic integrity and anti-tamper enforcement."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from goatee.core.parser import compute_contract_hash, read_contract_hash, parse_change_contract
from goatee.cli import build_parser, cmd_verify, cmd_approve


class TestContractIntegrity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=self.repo_root, check=True)

        (self.repo_root / "README.md").write_text("# Test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_root, check=True, capture_output=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text("Project-Name: TestProj\n", encoding="utf-8")

        change_dir = goatee_dir / "changes" / "001-integrity"
        change_dir.mkdir(parents=True)
        self.change_dir = change_dir
        self.change_file = change_dir / "change.md"

        self.contract_text = f"""# Change: 001-integrity

## Intent
Tamper test.

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
Contract-SHA256: 
"""
        self.change_file.write_text(self.contract_text, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_contract_hash_deterministic(self):
        h1 = compute_contract_hash(self.contract_text)
        h2 = compute_contract_hash(self.contract_text)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_contract_hash_ignores_mutable_state_fields(self):
        h_orig = compute_contract_hash(self.contract_text)
        modified_state = self.contract_text.replace("Baseline-Commit:", f"Baseline-Commit: {self.baseline_sha}")
        modified_state = modified_state.replace("Contract-SHA256:", f"Contract-SHA256: {h_orig}")
        h_after = compute_contract_hash(modified_state)
        self.assertEqual(h_orig, h_after)

    def test_contract_hash_detects_surface_tampering(self):
        h_orig = compute_contract_hash(self.contract_text)
        tampered_content = self.contract_text.replace("- src/app.py", "- src/app.py\n- src/secret.py")
        h_tampered = compute_contract_hash(tampered_content)
        self.assertNotEqual(h_orig, h_tampered)

    def test_approve_records_contract_hash(self):
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.repo_root)
            parser = build_parser()
            args = parser.parse_args(["approve", "001-integrity"])
            ret = cmd_approve(args)
            self.assertEqual(ret, 0)

            stored_hash = read_contract_hash(self.change_file)
            self.assertIsNotNone(stored_hash)
            self.assertEqual(len(stored_hash), 64)
            current_hash = compute_contract_hash(self.change_file.read_text(encoding="utf-8"))
            self.assertEqual(stored_hash, current_hash)
        finally:
            os.chdir(orig_cwd)

    def test_verify_halts_closed_on_tampered_contract(self):
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.repo_root)
            # 1. Approve
            parser = build_parser()
            cmd_approve(parser.parse_args(["approve", "001-integrity"]))

            # 2. Implement and commit
            src_dir = self.repo_root / "src"
            src_dir.mkdir()
            (src_dir / "app.py").write_text("# app\n", encoding="utf-8")
            (src_dir / "secret.py").write_text("# rogue file\n", encoding="utf-8")

            # Rogue agent edits change.md to secretly add src/secret.py to Expected Surface!
            tampered_text = self.change_file.read_text(encoding="utf-8").replace("- src/app.py", "- src/app.py\n- src/secret.py")
            self.change_file.write_text(tampered_text, encoding="utf-8")

            # Commit everything
            subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "feat: rogue implementation"], cwd=self.repo_root, check=True, capture_output=True)

            # 3. Verify must FAIL CLOSED with contract tamper violation!
            stderr_io = io.StringIO()
            with patch("sys.stderr", stderr_io):
                ret = cmd_verify(parser.parse_args(["verify", "001-integrity"]))

            self.assertEqual(ret, 1)
            self.assertIn("CONTRACT INTEGRITY VIOLATION", stderr_io.getvalue())
            self.assertIn("modified after human approval", stderr_io.getvalue())
        finally:
            os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
