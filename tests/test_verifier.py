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
        self.assertEqual(res.verdict, "NEEDS_REVIEW")
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
        self.assertEqual(res.verdict, "NEEDS_REVIEW")  # Because AC-02 failed
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
        self.assertEqual(res.verdict, "NEEDS_REVIEW")
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


if __name__ == "__main__":
    unittest.main()
