"""Exhaustive negative and regression tests for GoATee verification verdicts, CI governance, and landing invariants."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from goatee.core.ci import run_ci
from goatee.core.lifecycle import land_change, LifecycleError
from goatee.core.parser import compute_contract_hash, update_contract_state, parse_change_contract
from goatee.core.status import resolve_active_change_id
from goatee.core.attest import record_attestation
from goatee.core.verifier import verify_change, render_receipt_markdown, DEFAULT_VERIFICATION_TIMEOUT


class TestVerdictHardening(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        # Initialize Git repo
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=self.repo_root, check=True)

        (self.repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_root, check=True, capture_output=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        # Initialize GoATee project
        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text("Project-Name: VerdictTestProj\n", encoding="utf-8")
        (goatee_dir / "changes").mkdir()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_change(
        self,
        change_id: str,
        criteria_block: str,
        budget_files: list,
        blast_radius: list = None
    ) -> Path:
        change_dir = self.repo_root / ".goatee" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        contract_file = change_dir / "change.md"

        exp_surface = "\n".join(f"- {f}" for f in budget_files) if budget_files else "- (none)"
        br_surface = "\n".join(f"- {f}" for f in blast_radius) if blast_radius else "- (none)"

        content = f"""# Change: {change_id}

## Intent
Intent for {change_id}.

## Agreed Scope
### In Scope
- Scope

### Out of Scope
- None

## Acceptance Criteria
{criteria_block}

## Build Budget
### Expected Surface
{exp_surface}

### Reuse Strategy
- None

Dependency-Policy: No new runtime dependencies

### Blast Radius
{br_surface}

## Contract State
Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: {self.baseline_sha}
Contract-SHA256: 
"""
        contract_file.write_text(content, encoding="utf-8")
        c_hash = compute_contract_hash(contract_file.read_text(encoding="utf-8"))
        update_contract_state(contract_file, "Contract-SHA256", c_hash)
        return contract_file

    def test_failed_automated_check_yields_fail_verdict(self):
        """AC-01: An automated test that exits with non-zero code MUST yield verdict 'FAIL'."""
        criteria = """- AC-01: Failing test
  Evidence:
    type: automated
    command: python3 -c "import sys; sys.exit(1)"
    expected: exit 0"""

        self._create_change("001-fail-cmd", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add app.py"], cwd=self.repo_root, check=True, capture_output=True)

        contract = parse_change_contract(self.repo_root / ".goatee" / "changes" / "001-fail-cmd" / "change.md")
        result = verify_change(contract, self.repo_root)

        self.assertEqual(result.verdict, "FAIL")
        self.assertEqual(len(result.criterion_results), 1)
        self.assertEqual(result.criterion_results[0].status, "FAILED")

    def test_ci_allow_needs_review_fails_on_machine_failure(self):
        """AC-02: --allow-needs-review in CI MUST exit 1 when automated checks fail."""
        criteria = """- AC-01: Failing test
  Evidence:
    type: automated
    command: python3 -c "import sys; sys.exit(2)"
    expected: exit 0"""

        self._create_change("002-ci-fail", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add app.py"], cwd=self.repo_root, check=True, capture_output=True)

        res = run_ci(root=self.repo_root, change_id="002-ci-fail", allow_needs_review=True, write_step_summary=False)
        self.assertEqual(res.overall_status, "FAIL")
        self.assertEqual(res.exit_code, 1)

    def test_ci_human_accepted_does_not_override_failed_machine_evidence(self):
        """AC-03: review.md with Review-Verdict: ACCEPTED cannot override failed machine verification."""
        criteria = """- AC-01: Failing test
  Evidence:
    type: automated
    command: python3 -c "import sys; sys.exit(1)"
    expected: exit 0
- AC-02: Manual inspection
  Evidence:
    type: manual
    instruction: Check UI."""

        self._create_change("003-override-attempt", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add app.py"], cwd=self.repo_root, check=True, capture_output=True)

        head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True).stdout.strip()

        # Forge a review.md claiming ACCEPTED
        change_dir = self.repo_root / ".goatee" / "changes" / "003-override-attempt"
        review_text = f"""# Human Review: 003-override-attempt
Change-ID: 003-override-attempt
Review-Verdict: ACCEPTED
Updated-At: 2026-11-18 12:00:00Z

## Receipt Binding
Verified-Commit: {head_sha}
Receipt-Commit: {head_sha}
Receipt-SHA256: dummy_sha

## Attestation Coverage Matrix
| Criterion | Type | Machine Status | Human Attestation | Reviewer | Attested At | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AC-01** | automated | FAILED | **VERIFIED** | Reviewer | 2026-11-18 12:00:00Z | Overriding machine |
| **AC-02** | manual | NEEDS_REVIEW | **VERIFIED** | Reviewer | 2026-11-18 12:00:00Z | Manual ok |

## Review Event History
- Event: 1
  Attested-At: 2026-11-18 12:00:00Z
  Scope: criterion
  Criterion: AC-02
  Observation: VERIFIED
  Reviewer-Name: Reviewer
  Reviewer-Email: reviewer@example.com
  Attribution: Git config
  Note: Manual ok
"""
        (change_dir / "review.md").write_text(review_text, encoding="utf-8")

        res = run_ci(root=self.repo_root, change_id="003-override-attempt", allow_needs_review=True, write_step_summary=False)
        self.assertEqual(res.overall_status, "FAIL")
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.reports[0].status, "FAIL")

    def test_ambiguous_active_change_resolution_fails_closed(self):
        """AC-04: Multiple unverified changes without branch match MUST fail closed."""
        self._create_change("004-unverified-a", "- AC-01: Auto\n  Evidence:\n    type: automated\n    command: python3 -c \"exit(0)\"\n    expected: exit 0", ["a.py"])
        self._create_change("004-unverified-b", "- AC-01: Auto\n  Evidence:\n    type: automated\n    command: python3 -c \"exit(0)\"\n    expected: exit 0", ["b.py"])

        # When resolving active change on main without pointer file
        resolved = resolve_active_change_id(self.repo_root)
        self.assertIsNone(resolved)

        # CI without --change-id must fail closed with unambiguous report
        res = run_ci(root=self.repo_root, write_step_summary=False)
        self.assertEqual(res.overall_status, "FAIL")
        self.assertEqual(res.exit_code, 1)
        self.assertIn("No active change contract could be unambiguously resolved", res.reports[0].error_message)
        self.assertIn("--all", res.reports[0].error_message)

    def test_land_change_validates_receipt_and_integrity(self):
        """AC-05: land_change validates receipt success, hash binding, and contract integrity."""
        criteria = """- AC-01: Pass test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0
- AC-02: Manual inspection
  Evidence:
    type: manual
    instruction: Verify visually."""

        self._create_change("005-land", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "005-land"
        contract = parse_change_contract(change_dir / "change.md")

        # 1. Verification generates receipt with NEEDS_REVIEW (due to manual AC-02)
        v_res = verify_change(contract, self.repo_root)
        self.assertEqual(v_res.verdict, "NEEDS_REVIEW")

        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)
        receipt_file = change_dir / "receipt.md"
        receipt_file.write_text(receipt_text, encoding="utf-8")

        subprocess.run(["git", "add", str(receipt_file)], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add receipt"], cwd=self.repo_root, check=True, capture_output=True)

        # Attempt to land without review.md MUST fail
        with self.assertRaises(LifecycleError) as ctx:
            land_change(self.repo_root, "005-land")
        self.assertIn("human review missing", str(ctx.exception))

        # 2. Add valid review.md using record_attestation
        record_attestation(self.repo_root, "005-land", "AC-02", verified=True, rejected=False, note="Visual ok")
        subprocess.run(["git", "add", str(change_dir / "review.md")], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add review"], cwd=self.repo_root, check=True, capture_output=True)

        # Land must now succeed cleanly!
        dest = land_change(self.repo_root, "005-land")
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "change.md").is_file())
        self.assertIn("Lifecycle-Status: LANDED", (dest / "change.md").read_text(encoding="utf-8"))

    def test_timeout_yields_fail_verdict(self):
        """AC-06: Verification timeout yields verdict 'FAIL'."""
        criteria = """- AC-01: Sleep timeout
  Evidence:
    type: automated
    command: python3 -c "import time; time.sleep(10)"
    expected: exit 0"""

        self._create_change("006-timeout", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        contract = parse_change_contract(self.repo_root / ".goatee" / "changes" / "006-timeout" / "change.md")

        with patch("goatee.core.verifier.DEFAULT_VERIFICATION_TIMEOUT", 0.5):
            res = verify_change(contract, self.repo_root)

        self.assertEqual(res.verdict, "FAIL")
        self.assertIn("TIMEOUT", res.criterion_results[0].observed)

    def test_budget_violation_yields_fail_verdict(self):
        """AC-06: Unapproved surface modification yields verdict 'FAIL'."""
        criteria = """- AC-01: Pass test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("007-budget-fail", criteria, ["app.py"], blast_radius=["secret.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        (self.repo_root / "secret.py").write_text("secret_key = 123\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: violate blast radius"], cwd=self.repo_root, check=True, capture_output=True)

        contract = parse_change_contract(self.repo_root / ".goatee" / "changes" / "007-budget-fail" / "change.md")
        res = verify_change(contract, self.repo_root)

        self.assertEqual(res.verdict, "FAIL")
        self.assertIn("secret.py", res.budget_result.protected_changes)

    def test_git_mutation_yields_fail_verdict(self):
        """AC-06: Test command that mutates Git state yields verdict 'FAIL'."""
        criteria = """- AC-01: Mutating test
  Evidence:
    type: automated
    command: python3 -c "open('untracked_mutation.txt', 'w').write('bad')"
    expected: exit 0"""

        self._create_change("008-mutation-fail", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        contract = parse_change_contract(self.repo_root / ".goatee" / "changes" / "008-mutation-fail" / "change.md")
        res = verify_change(contract, self.repo_root)

        self.assertEqual(res.verdict, "FAIL")
        self.assertTrue(any("untracked_mutation.txt" in m for m in res.mutations))

    def test_post_verification_implementation_modification_blocks_landing(self):
        """AC-06: Modifying implementation after receipt generation prevents land_change."""
        criteria = """- AC-01: Pass test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("009-post-mod", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('v1')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: v1"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "009-post-mod"
        contract = parse_change_contract(change_dir / "change.md")

        v_res = verify_change(contract, self.repo_root)
        self.assertEqual(v_res.verdict, "PASS")

        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)
        (change_dir / "receipt.md").write_text(receipt_text, encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: receipt"], cwd=self.repo_root, check=True, capture_output=True)

        # Sneakily modify application file AFTER verification
        (self.repo_root / "app.py").write_text("print('v2 rogue modified')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: rogue post-verification edit"], cwd=self.repo_root, check=True, capture_output=True)

        with self.assertRaises(LifecycleError) as ctx:
            land_change(self.repo_root, "009-post-mod")
        self.assertIn("implementation was modified after verification", str(ctx.exception))

    def test_valid_flow_implementation_receipt_review_ci_pass(self):
        """Review Finding 1: Valid implementation -> receipt commit -> review commit -> CI PASS."""
        criteria = """- AC-01: Auto check
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0
- AC-02: Manual check
  Evidence:
    type: manual
    instruction: Check documentation."""

        self._create_change("010-valid-flow", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('flow v1')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: implement app.py"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "010-valid-flow"
        contract = parse_change_contract(change_dir / "change.md")

        # 1. Verification at implementation commit
        v_res = verify_change(contract, self.repo_root)
        self.assertEqual(v_res.verdict, "NEEDS_REVIEW")

        # 2. Write receipt and commit it
        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)
        (change_dir / "receipt.md").write_text(receipt_text, encoding="utf-8")
        subprocess.run(["git", "add", ".goatee/changes/010-valid-flow/receipt.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add receipt"], cwd=self.repo_root, check=True, capture_output=True)

        # 3. Attest manual check and commit review
        record_attestation(self.repo_root, "010-valid-flow", "AC-02", verified=True, rejected=False, note="Manual ok")
        subprocess.run(["git", "add", ".goatee/changes/010-valid-flow/review.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add review"], cwd=self.repo_root, check=True, capture_output=True)

        # 4. CI must now PASS even without --allow-needs-review, recognizing valid dual-verdict acceptance
        res = run_ci(root=self.repo_root, change_id="010-valid-flow", allow_needs_review=False, write_step_summary=False)
        self.assertEqual(res.overall_status, "PASS")
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.reports[0].status, "PASS")
        self.assertEqual(res.reports[0].human_verdict, "ACCEPTED")

    def test_review_verdict_inconsistent_with_events_fails(self):
        """Review Finding 2: Review verdict inconsistent with event history fails validation in CI and landing."""
        criteria = """- AC-01: Auto check
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0
- AC-02: Manual check
  Evidence:
    type: manual
    instruction: Check documentation."""

        self._create_change("011-tampered-review", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "011-tampered-review"
        contract = parse_change_contract(change_dir / "change.md")

        v_res = verify_change(contract, self.repo_root)
        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)
        (change_dir / "receipt.md").write_text(receipt_text, encoding="utf-8")
        subprocess.run(["git", "add", ".goatee/changes/011-tampered-review/receipt.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add receipt"], cwd=self.repo_root, check=True, capture_output=True)

        # Record a REJECTION in event history
        record_attestation(self.repo_root, "011-tampered-review", "AC-02", verified=False, rejected=True, note="Failed review")

        # Now tamper with review.md: manually flip header to ACCEPTED while event history has REJECTED
        review_file = change_dir / "review.md"
        original_review = review_file.read_text(encoding="utf-8")
        tampered_review = original_review.replace("Review-Verdict: REJECTED", "Review-Verdict: ACCEPTED")
        review_file.write_text(tampered_review, encoding="utf-8")

        subprocess.run(["git", "add", str(review_file)], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: tampered review"], cwd=self.repo_root, check=True, capture_output=True)

        # CI must reject the inconsistent projection
        res = run_ci(root=self.repo_root, change_id="011-tampered-review", write_step_summary=False)
        self.assertEqual(res.overall_status, "FAIL")
        self.assertEqual(res.exit_code, 1)
        self.assertIn("Review integrity violation", res.reports[0].error_message)

        # Landing must also abort fail-closed
        with self.assertRaises(LifecycleError) as ctx:
            land_change(self.repo_root, "011-tampered-review")
        self.assertIn("review validation failed", str(ctx.exception))

    def test_git_inspection_failure_during_landing_aborts(self):
        """Review Finding 3: Git status or diff command failures during landing abort fail-closed."""
        criteria = """- AC-01: Pass test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("012-git-fail", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "012-git-fail"
        contract = parse_change_contract(change_dir / "change.md")
        v_res = verify_change(contract, self.repo_root)
        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)
        (change_dir / "receipt.md").write_text(receipt_text, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: receipt"], cwd=self.repo_root, check=True, capture_output=True)

        # Simulate git diff error during landing inspection
        real_run = subprocess.run
        def mock_subprocess_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[0] == "git" and cmd[1] in ("diff", "status"):
                return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: simulated git corruption")
            return real_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with self.assertRaises(LifecycleError) as ctx:
                land_change(self.repo_root, "012-git-fail")
            self.assertIn("Git inspection error during landing", str(ctx.exception))

    def test_missing_contract_hash_or_unapproved_state_blocks_landing(self):
        """Review Finding 3: Missing contract hash or unapproved state blocks landing."""
        criteria = """- AC-01: Pass test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("013-unapproved", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "013-unapproved"
        contract = parse_change_contract(change_dir / "change.md")
        v_res = verify_change(contract, self.repo_root)
        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)
        (change_dir / "receipt.md").write_text(receipt_text, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: receipt"], cwd=self.repo_root, check=True, capture_output=True)

        # Case A: Contract is DRAFT
        contract_file = change_dir / "change.md"
        c_text = contract_file.read_text(encoding="utf-8")
        contract_file.write_text(c_text.replace("Agreement-Status: APPROVED", "Agreement-Status: DRAFT"), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: unapprove agreement"], cwd=self.repo_root, check=True, capture_output=True)

        with self.assertRaises(LifecycleError) as ctx:
            land_change(self.repo_root, "013-unapproved")
        self.assertIn("Agreement-Status is 'DRAFT'. Must be 'APPROVED'", str(ctx.exception))

        # Case B: Missing Contract-SHA256
        old_hash = compute_contract_hash(c_text)
        contract_file.write_text(c_text.replace(f"Contract-SHA256: {old_hash}", "Contract-SHA256: "), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: clear hash"], cwd=self.repo_root, check=True, capture_output=True)

        with self.assertRaises(LifecycleError) as ctx:
            land_change(self.repo_root, "013-unapproved")
        self.assertIn("contract has no locked Contract-SHA256", str(ctx.exception))

    def test_receipt_with_not_run_criteria_blocks_landing(self):
        """Review Finding 3: Receipt with NOT_RUN automated criteria blocks landing."""
        criteria = """- AC-01: Pass test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("014-not-run", criteria, ["app.py"])
        (self.repo_root / "app.py").write_text("print('app')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: app.py"], cwd=self.repo_root, check=True, capture_output=True)

        change_dir = self.repo_root / ".goatee" / "changes" / "014-not-run"
        contract = parse_change_contract(change_dir / "change.md")
        v_res = verify_change(contract, self.repo_root)
        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        receipt_text = render_receipt_markdown(v_res, template_path)

        # Alter receipt row to NOT_RUN
        receipt_text = receipt_text.replace("**VERIFIED**", "**NOT_RUN**")
        (change_dir / "receipt.md").write_text(receipt_text, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: receipt with not-run"], cwd=self.repo_root, check=True, capture_output=True)

        with self.assertRaises(LifecycleError) as ctx:
            land_change(self.repo_root, "014-not-run")
        self.assertIn("was NOT_RUN", str(ctx.exception))

    def test_clean_main_branch_ci_with_unverified_historical_changes_fails_closed(self):
        """Review Finding 4: Clean main-branch CI with historical unverified changes fails closed, refusing to guess."""
        self._create_change(
            "002-handoff-budget",
            "- AC-01: Hist\n  Evidence:\n    type: automated\n    command: python3 -c \"exit(0)\"\n    expected: exit 0",
            ["old.py"]
        )
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add historical 002-handoff-budget"], cwd=self.repo_root, check=True, capture_output=True)

        # Ensure active_change is None on clean trunk
        active = resolve_active_change_id(self.repo_root)
        self.assertIsNone(active)

        # Run CI on main without explicit target change
        res = run_ci(root=self.repo_root, write_step_summary=False)
        self.assertEqual(res.overall_status, "FAIL")
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.reports[0].change_id, "(unresolved)")
        self.assertIn("--all", res.reports[0].error_message)
        # Ensure 002-handoff-budget was NOT silently selected
        self.assertNotIn("002-handoff-budget", [r.change_id for r in res.reports if r.change_id != "(unresolved)"])


if __name__ == "__main__":
    unittest.main()
