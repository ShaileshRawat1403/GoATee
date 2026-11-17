"""Unit and integration tests for interactive human attestation wizard."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from goatee.core.attest import run_interactive_attestation, load_and_validate_review
from goatee.core.parser import parse_change_contract
from goatee.core.status import parse_receipt


class TestInteractiveAttestation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Jane Reviewer"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "jane@example.com"], cwd=self.repo_root, check=True)

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
        change_dir = goatee_dir / "changes" / "001-attest"
        change_dir.mkdir(parents=True)
        self.change_dir = change_dir

        contract_content = f"""# Change: 001-attest

## Intent
Test interactive attestation.

## Agreed Scope
### In Scope
- Work

### Out of Scope
- None

## Acceptance Criteria
- AC-01: Automated check
  Evidence:
    type: automated
    command: python3 -c "print('ok')"
    expected: exit 0

- AC-02: Manual visual check
  Evidence:
    type: manual
    instruction: Verify UI elements render properly.

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
Baseline-Commit: {self.baseline_sha}
"""
        (change_dir / "change.md").write_text(contract_content, encoding="utf-8")

        # Create delivered file
        src_dir = self.repo_root / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# app\n", encoding="utf-8")

        # Commit delivered implementation and contract
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: implement change"], cwd=self.repo_root, check=True, capture_output=True)

        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.verified_sha = res_head.stdout.strip()

        # Create receipt.md
        receipt_content = f"""# Delivery Receipt: 001-attest

Verdict: NEEDS_REVIEW
Verified-At: 2026-11-17 12:00:00Z

## Commit Provenance
Baseline-Commit: {self.baseline_sha}
Verified-Commit: {self.verified_sha}

## Requirement Coverage Matrix
| Criterion | Type | Declared | Observed | Status |
| :--- | :--- | :--- | :--- | :--- |
| **AC-01** | automated | `python3 -c "print('ok')"` | exit 0 (0.01s) | **VERIFIED** |
| **AC-02** | manual | `Verify UI elements render properly.` | Pending human observation and sign-off | **NEEDS_REVIEW** |

## Build Budget & Surface Analysis
### Expected Changes
- src/app.py

### Surface Deviations
- (none)

### Protected Changes
- (none)

## Git-Visible Mutation Report
- Zero Git-visible repository mutations detected during verification.

## Diagnostic Excerpt
- No diagnostics recorded.
"""
        (change_dir / "receipt.md").write_text(receipt_content, encoding="utf-8")

        # Track and commit receipt.md
        subprocess.run(["git", "add", ".goatee/changes/001-attest/receipt.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit receipt"], cwd=self.repo_root, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_interactive_attest_manual_criterion_verified(self):
        inputs = ["v", "Visual layout verified cleanly."]
        outputs = []

        def mock_input(prompt):
            outputs.append(prompt)
            return inputs.pop(0)

        def mock_output(msg):
            outputs.append(msg)

        ret = run_interactive_attestation(
            repo_root=self.repo_root,
            change_id="001-attest",
            input_fn=mock_input,
            output_fn=mock_output
        )

        self.assertEqual(ret, 0)
        review_file = self.change_dir / "review.md"
        self.assertTrue(review_file.is_file())
        review_text = review_file.read_text(encoding="utf-8")
        self.assertIn("Review-Verdict: ACCEPTED", review_text)
        self.assertIn("Visual layout verified cleanly.", review_text)
        self.assertIn("Jane Reviewer", review_text)

    def test_interactive_attest_skip_records_no_events(self):
        inputs = ["s"]
        outputs = []

        def mock_input(prompt):
            outputs.append(prompt)
            return inputs.pop(0)

        def mock_output(msg):
            outputs.append(msg)

        ret = run_interactive_attestation(
            repo_root=self.repo_root,
            change_id="001-attest",
            input_fn=mock_input,
            output_fn=mock_output
        )

        self.assertEqual(ret, 0)
        review_file = self.change_dir / "review.md"
        self.assertFalse(review_file.is_file())
        self.assertTrue(any("No new attestation events recorded" in m for m in outputs))


if __name__ == "__main__":
    unittest.main()
