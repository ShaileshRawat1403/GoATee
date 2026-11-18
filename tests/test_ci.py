"""Unit tests for GoATee Continuous Integration (CI) engine and CLI."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from goatee.core.ci import (
    run_ci,
    format_github_step_summary,
    write_github_step_summary,
    ci_result_to_dict,
)
from goatee.core.parser import compute_contract_hash, update_contract_state


class TestCIEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        # Initialize Git repo
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "CI Tester"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "ci@tester.local"], cwd=self.repo_root, check=True)

        (self.repo_root / "README.md").write_text("# CI Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_root, check=True, capture_output=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        # Initialize GoATee project
        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text("Project-Name: CITestProject\n", encoding="utf-8")
        (goatee_dir / "changes").mkdir()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_change(self, change_id: str, criteria_block: str, budget_files: list) -> Path:
        change_dir = self.repo_root / ".goatee" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        contract_file = change_dir / "change.md"

        exp_surface = "\n".join(f"- {f}" for f in budget_files)
        content = f"""# Change: {change_id}

## Intent
Intent for {change_id}.

## Agreed Scope
### In Scope
- Testing CI workflow

### Out of Scope
- None

## Acceptance Criteria
{criteria_block}

## Build Budget
### Expected Surface
{exp_surface}

### Reuse Strategy
- Zero external dependencies

Dependency-Policy: No new runtime dependencies

### Blast Radius
- (none)

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

    def test_ci_pass_clean(self):
        # Create passing automated check
        criteria = """- AC-01: Pass always
  Evidence:
    type: automated
    command: python3 -c "import sys; sys.exit(0)"
    expected: exit 0"""

        self._create_change("001-pass", criteria, ["app.py"])

        # Make commit for app.py
        (self.repo_root / "app.py").write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add app.py"], cwd=self.repo_root, check=True, capture_output=True)

        res = run_ci(root=self.repo_root, change_id="001-pass", write_step_summary=False)
        self.assertEqual(res.overall_status, "PASS")
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(res.reports), 1)
        self.assertEqual(res.reports[0].status, "PASS")

    def test_ci_needs_review_without_allow(self):
        # Create manual criterion
        criteria = """- AC-01: Manual inspection
  Evidence:
    type: manual
    instruction: Inspect the design manually."""

        self._create_change("002-manual", criteria, ["ui.py"])

        (self.repo_root / "ui.py").write_text("# UI code\n", encoding="utf-8")
        subprocess.run(["git", "add", "ui.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add ui.py"], cwd=self.repo_root, check=True, capture_output=True)

        res = run_ci(root=self.repo_root, change_id="002-manual", allow_needs_review=False, write_step_summary=False)
        self.assertEqual(res.overall_status, "NEEDS_REVIEW")
        self.assertEqual(res.exit_code, 1)

    def test_ci_needs_review_with_allow(self):
        criteria = """- AC-01: Manual inspection
  Evidence:
    type: manual
    instruction: Inspect the design manually."""

        self._create_change("003-allowed", criteria, ["ui2.py"])

        (self.repo_root / "ui2.py").write_text("# UI code 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "ui2.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add ui2.py"], cwd=self.repo_root, check=True, capture_output=True)

        res = run_ci(root=self.repo_root, change_id="003-allowed", allow_needs_review=True, write_step_summary=False)
        self.assertEqual(res.overall_status, "NEEDS_REVIEW_ALLOWED")
        self.assertEqual(res.exit_code, 0)

    def test_ci_github_step_summary(self):
        criteria = """- AC-01: Quick test
  Evidence:
    type: automated
    command: python3 -c "print('ci ok')"
    expected: exit 0"""

        self._create_change("004-summary", criteria, ["mod.py"])
        (self.repo_root / "mod.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "mod.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: mod"], cwd=self.repo_root, check=True, capture_output=True)

        summary_file = self.repo_root / "step_summary.md"
        old_env = os.environ.get("GITHUB_STEP_SUMMARY")
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary_file)
            res = run_ci(root=self.repo_root, change_id="004-summary", write_step_summary=True)
            self.assertEqual(res.overall_status, "PASS")
            self.assertTrue(summary_file.is_file())
            content = summary_file.read_text(encoding="utf-8")
            self.assertIn("GoATee Delivery &amp; Governance CI Summary", content)
            self.assertIn("004-summary", content)
            self.assertIn("Requirement Coverage Matrix", content)
        finally:
            if old_env is not None:
                os.environ["GITHUB_STEP_SUMMARY"] = old_env
            else:
                os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_ci_json_output(self):
        criteria = """- AC-01: Quick test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("005-json", criteria, ["data.py"])
        (self.repo_root / "data.py").write_text("y = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "data.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: data"], cwd=self.repo_root, check=True, capture_output=True)

        res = run_ci(root=self.repo_root, change_id="005-json", write_step_summary=False)
        data = ci_result_to_dict(res)
        self.assertEqual(data["overall_status"], "PASS")
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(len(data["reports"]), 1)
        self.assertEqual(data["reports"][0]["change_id"], "005-json")
        self.assertIn("verification", data["reports"][0])

    def test_ci_contract_integrity_violation(self):
        criteria = """- AC-01: Test
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        contract_file = self._create_change("006-tamper", criteria, ["app.py"])
        # Tamper with contract without updating hash
        content = contract_file.read_text(encoding="utf-8")
        contract_file.write_text(content.replace("Intent for 006-tamper.", "Tampered unauthorized intent."), encoding="utf-8")

        res = run_ci(root=self.repo_root, change_id="006-tamper", write_step_summary=False)
        self.assertEqual(res.overall_status, "FAIL")
        self.assertEqual(res.exit_code, 1)
        self.assertIn("Contract integrity violation", res.reports[0].error_message)

    def test_cli_ci_execution(self):
        criteria = """- AC-01: Pass
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("007-cli", criteria, ["cli_app.py"])
        (self.repo_root / "cli_app.py").write_text("# cli\n", encoding="utf-8")
        subprocess.run(["git", "add", "cli_app.py"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: cli"], cwd=self.repo_root, check=True, capture_output=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)

        # Run goatee ci --json 007-cli
        summary_out = self.repo_root / "out_summary.md"
        res = subprocess.run(
            [
                "python3", "-m", "goatee.cli", "ci", "007-cli",
                "--json",
                "--output-summary", str(summary_out)
            ],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["overall_status"], "PASS")
        self.assertTrue(summary_out.is_file())
        self.assertIn("GoATee Delivery", summary_out.read_text(encoding="utf-8"))

    def test_cli_verify_ci_flag(self):
        criteria = """- AC-01: Pass
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0"""

        self._create_change("008-vci", criteria, ["vci.py"])
        (self.repo_root / "vci.py").write_text("# vci\n", encoding="utf-8")
        subprocess.run(["git", "add", "vci.py", ".goatee"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: vci and contract"], cwd=self.repo_root, check=True, capture_output=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)

        res = subprocess.run(
            ["python3", "-m", "goatee.cli", "verify", "008-vci", "--ci", "--json"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        self.assertIn('"verdict": "PASS"', res.stdout)

    def test_github_actions_pr_event_path_success(self):
        """Workflow Path 1a: pull_request event resolves change via GITHUB_HEAD_REF and succeeds with --allow-needs-review."""
        criteria = """- AC-01: Auto pass
  Evidence:
    type: automated
    command: python3 -c "exit(0)"
    expected: exit 0
- AC-02: Manual review
  Evidence:
    type: manual
    instruction: Check visually."""

        self._create_change("009-pr-flow", criteria, ["pr_app.py"])
        (self.repo_root / "pr_app.py").write_text("# pr\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: pr code"], cwd=self.repo_root, check=True, capture_output=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        env["GITHUB_HEAD_REF"] = "feat/009-pr-flow"

        res = subprocess.run(
            ["python3", "-m", "goatee.cli", "ci", "--allow-needs-review"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        self.assertIn("009-pr-flow", res.stdout)
        self.assertIn("NEEDS REVIEW (ALLOWED)", res.stdout)

    def test_github_actions_pr_event_path_unresolved_fails_closed(self):
        """Workflow Path 1b: pull_request with unknown branch fails closed without guessing."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        env["GITHUB_HEAD_REF"] = "feat/unknown-nonexistent-branch"

        res = subprocess.run(
            ["python3", "-m", "goatee.cli", "ci", "--allow-needs-review"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("No active change contract could be unambiguously resolved", res.stdout)

    def test_github_actions_pr_event_path_machine_fail_exits_one(self):
        """Workflow Path 1c: pull_request with failing automated checks exits 1 even with --allow-needs-review."""
        criteria = """- AC-01: Auto fail
  Evidence:
    type: automated
    command: python3 -c "import sys; sys.exit(1)"
    expected: exit 0"""

        self._create_change("010-pr-fail", criteria, ["fail_app.py"])
        (self.repo_root / "fail_app.py").write_text("# fail\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "feat: failing pr code"], cwd=self.repo_root, check=True, capture_output=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        env["GITHUB_HEAD_REF"] = "feat/010-pr-fail"

        res = subprocess.run(
            ["python3", "-m", "goatee.cli", "ci", "--allow-needs-review"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("FAILED", res.stdout)

    def test_github_actions_trunk_push_event_path(self):
        """Workflow Path 2: On trunk (push to main), status check verifies project health."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        if "GITHUB_HEAD_REF" in env:
            del env["GITHUB_HEAD_REF"]

        # 1. Healthy project status succeeds
        res = subprocess.run(
            ["python3", "-m", "goatee.cli", "status"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        self.assertIn("Project: CITestProject", res.stdout)

        # 2. Corrupted project status fails closed
        (self.repo_root / ".goatee" / "project.md").write_text("Invalid Corrupted Syntax Without Colon", encoding="utf-8")
        res_fail = subprocess.run(
            ["python3", "-m", "goatee.cli", "status"],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True
        )
        self.assertNotEqual(res_fail.returncode, 0)


if __name__ == "__main__":
    unittest.main()
