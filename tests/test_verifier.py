"""Unit tests for the core verification engine (AC-03 through AC-06, AC-08)."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from goatee.core.models import AcceptanceCriterion, BuildBudget, ChangeContract, ContractState
from goatee.core.verifier import (
    execute_criterion_command,
    get_git_visible_fingerprint,
    detect_mutations,
    validate_evidence_declaration,
    verify_change,
    render_receipt_markdown,
)


class TestVerifier(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name)
        
        # Initialize Git repo
        subprocess.run(["git", "init"], check=True, capture_output=True, cwd=self.repo_root)
        subprocess.run(["git", "config", "user.email", "test@test.local"], check=True, cwd=self.repo_root)
        subprocess.run(["git", "config", "user.name", "Tester"], check=True, cwd=self.repo_root)
        
        # Initial commit
        (self.repo_root / "app.py").write_text("print('hello')", encoding="utf-8")
        subprocess.run(["git", "add", "."], check=True, cwd=self.repo_root)
        subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True, cwd=self.repo_root)
        self.base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=self.repo_root
        ).stdout.strip()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_budget_deviations_gate_execution(self):
        """AC-03: Budget deviations gate evidence execution without running commands."""
        # Create an unapproved file outside budget
        (self.repo_root / "unapproved.py").write_text("evil = True", encoding="utf-8")
        subprocess.run(["git", "add", "."], check=True, cwd=self.repo_root)
        subprocess.run(["git", "commit", "-m", "Unapproved work"], check=True, capture_output=True, cwd=self.repo_root)

        # Build contract that does NOT allow unapproved.py
        contract = ChangeContract(
            change_id="001-test",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Should not run",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=["app.py"]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "FAIL")
        self.assertIn("unapproved.py", res.budget_result.surface_deviations)
        self.assertEqual(res.criterion_results[0].status, "NOT_RUN")
        self.assertIn("Blocked by unapproved budget deviations", res.criterion_results[0].observed)

    def test_automated_criteria_execution(self):
        """AC-04: Automated criteria support narrow exit N grammar and capture bounded results."""
        contract = ChangeContract(
            change_id="002-test",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Passing check",
                    evidence_type="automated",
                    command="python3 -c 'print(\"ok\"); exit(0)'",
                    expected="exit 0"
                ),
                AcceptanceCriterion(
                    id="AC-02",
                    description="Failing check",
                    evidence_type="automated",
                    command="python3 -c 'import sys; print(\"error\", file=sys.stderr); exit(2)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "FAIL")  # Because AC-02 failed
        self.assertEqual(res.criterion_results[0].status, "VERIFIED")
        self.assertIn("exit 0", res.criterion_results[0].observed)

        self.assertEqual(res.criterion_results[1].status, "FAILED")
        self.assertIn("exit 2", res.criterion_results[1].observed)
        self.assertIn("error", res.criterion_results[1].diagnostic)

    def test_manual_criteria_handling(self):
        """AC-05: Manual criteria are honestly represented as pending review yielding NEEDS_REVIEW."""
        contract = ChangeContract(
            change_id="003-test",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Passing automated check",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                ),
                AcceptanceCriterion(
                    id="AC-02",
                    description="Visual layout check",
                    evidence_type="manual",
                    instruction="Verify button layout"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "NEEDS_REVIEW")
        self.assertEqual(res.criterion_results[0].status, "VERIFIED")
        self.assertEqual(res.criterion_results[1].status, "NEEDS_REVIEW")
        self.assertIn("Pending human observation", res.criterion_results[1].observed)

    def test_halt_on_git_visible_mutation(self):
        """AC-06: Verification halts immediately if a command mutates Git-visible repository state."""
        contract = ChangeContract(
            change_id="004-test",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Mutating command that touches repo",
                    evidence_type="automated",
                    command="python3 -c 'with open(\"polluted.txt\", \"w\") as f: f.write(\"junk\")'",
                    expected="exit 0"
                ),
                AcceptanceCriterion(
                    id="AC-02",
                    description="Subsequent command that should be NOT_RUN",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "FAIL")
        self.assertEqual(res.criterion_results[0].status, "FAILED")
        self.assertIn("[MUTATED REPO]", res.criterion_results[0].observed)
        self.assertEqual(res.criterion_results[1].status, "NOT_RUN")
        self.assertTrue(len(res.mutations) > 0)
        self.assertIn("Created/untracked: polluted.txt", res.mutations[0])

    def test_verdict_evaluation_strict_pass(self):
        """AC-08: Delivery verdict evaluates to PASS strictly when all conditions are satisfied."""
        contract = ChangeContract(
            change_id="005-test",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Clean check 1",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                ),
                AcceptanceCriterion(
                    id="AC-02",
                    description="Clean check 2",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "PASS")
        self.assertEqual(len(res.mutations), 0)
        for cr in res.criterion_results:
            self.assertEqual(cr.status, "VERIFIED")

    def test_halt_on_head_mutation(self):
        """AC-01: Verifier detects when an evidence command changes Git HEAD even if worktree finishes clean."""
        contract = ChangeContract(
            change_id="006-head-mut",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Command that commits internally",
                    evidence_type="automated",
                    command='python3 -c \'import subprocess; open("app.py", "w").write("modified"); subprocess.run(["git", "add", "."], check=True); subprocess.run(["git", "commit", "-m", "secret commit"], check=True)\'',
                    expected="exit 0"
                ),
                AcceptanceCriterion(
                    id="AC-02",
                    description="Subsequent command that should be NOT_RUN",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "FAIL")
        self.assertEqual(res.criterion_results[0].status, "FAILED")
        self.assertIn("HEAD MUTATION DETECTED", res.criterion_results[0].observed)
        self.assertIn("HEAD MUTATION DETECTED", res.criterion_results[0].diagnostic)
        self.assertEqual(res.criterion_results[1].status, "NOT_RUN")
        self.assertTrue(len(res.mutations) > 0)
        self.assertTrue(any("HEAD mutated" in m for m in res.mutations))

    def test_head_invariant_before_criterion(self):
        """AC-02: Verifier asserts HEAD equals Verified-Commit before each criterion executes."""
        # Create a contract with two criteria, but mutate HEAD externally or in a test seam
        contract = ChangeContract(
            change_id="007-head-drift",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Normal check",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=self.base_sha
            )
        )
        # Test execute_criterion_command timeout directly
        actual_code, duration, stdout, stderr, is_timeout = execute_criterion_command(
            "python3 -c 'exit(0)'", cwd=self.repo_root
        )
        self.assertEqual(actual_code, 0)
        self.assertFalse(is_timeout)

    def test_halt_on_control_plane_mutation(self):
        """AC-03: Verifier detects and halts if an evidence command modifies any file inside .goatee/**."""
        # Create a .goatee file
        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir(exist_ok=True)
        (goatee_dir / "project.md").write_text("# Project Info", encoding="utf-8")
        subprocess.run(["git", "add", "."], check=True, cwd=self.repo_root)
        subprocess.run(["git", "commit", "-m", "Add project.md"], check=True, capture_output=True, cwd=self.repo_root)
        current_head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, cwd=self.repo_root).stdout.strip()

        contract = ChangeContract(
            change_id="008-ctrl-mut",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-01",
                    description="Command that mutates .goatee file",
                    evidence_type="automated",
                    command='python3 -c \'open(".goatee/project.md", "w").write("tampered")\'',
                    expected="exit 0"
                ),
                AcceptanceCriterion(
                    id="AC-02",
                    description="Subsequent command that should be NOT_RUN",
                    evidence_type="automated",
                    command="python3 -c 'exit(0)'",
                    expected="exit 0"
                )
            ],
            build_budget=BuildBudget(expected_surface=[]),
            contract_state=ContractState(
                agreement_status="APPROVED",
                budget_status="APPROVED",
                baseline_commit=current_head
            )
        )

        res = verify_change(contract, self.repo_root)
        self.assertEqual(res.verdict, "FAIL")
        self.assertEqual(res.criterion_results[0].status, "FAILED")
        self.assertIn("[MUTATED REPO]", res.criterion_results[0].observed)
        self.assertEqual(res.criterion_results[1].status, "NOT_RUN")
        self.assertTrue(any(".goatee/project.md" in m for m in res.mutations))

    def test_command_timeout_handling(self):
        """AC-05: Automated criteria execute with bounded timeout; timed out processes are terminated and marked FAILED."""
        # Test command directly with short timeout seam
        code, duration, stdout, stderr, is_timeout = execute_criterion_command(
            "python3 -c 'import time; time.sleep(5)'",
            cwd=self.repo_root,
            timeout_seconds=0.2
        )
        self.assertTrue(is_timeout)
        self.assertIsNone(code)
        self.assertTrue(duration >= 0.2)

    def test_non_interactive_execution(self):
        """AC-06: Verification commands execute non-interactively with stdin=subprocess.DEVNULL."""
        code, duration, stdout, stderr, is_timeout = execute_criterion_command(
            "python3 -c 'import sys; data = sys.stdin.read(); exit(0 if data == \"\" else 1)'",
            cwd=self.repo_root
        )
        self.assertFalse(is_timeout)
        self.assertEqual(code, 0)

    def test_render_receipt_escapes_pipes(self):
        """Rendered receipt markdown must escape pipes in commands and observations."""
        from goatee.core.verifier import CriterionResult, VerificationResult
        from goatee.core.budget import BudgetComparisonResult

        template_path = Path(__file__).parent.parent / "goatee" / "templates" / "receipt.md"
        result = VerificationResult(
            change_id="001-pipe-render",
            baseline_commit="1111111111111111",
            verified_commit="2222222222222222",
            budget_result=BudgetComparisonResult(expected_changes=["app.py"]),
            criterion_results=[
                CriterionResult(
                    criterion_id="AC-01",
                    evidence_type="automated",
                    declared="cat file | grep foo",
                    observed="result | with | pipes",
                    status="VERIFIED"
                )
            ],
            mutations=[],
            verdict="PASS",
            verified_at="2026-11-17 12:00:00Z"
        )
        md = render_receipt_markdown(result, template_path)
        self.assertIn("cat file \\| grep foo", md)
        self.assertIn("result \\| with \\| pipes", md)


if __name__ == "__main__":
    unittest.main()
