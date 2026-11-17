"""Unit and integration tests for GoATee zero-side-effect status inspection."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from goatee.core.parser import parse_change_contract
from goatee.core.status import (
    ControlStateError,
    ReceiptSyntaxError,
    find_project_root,
    get_change_report,
    get_project_status,
    format_change_detail,
    format_project_overview,
    parse_project_name,
    parse_receipt,
    validate_contract_receipt_consistency,
)


def _init_git_repo(path: Path) -> None:
    """Helper to initialize a clean git repository."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@goatee.dev"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "GoATee Test"], cwd=path, check=True)


SAMPLE_PROJECT_MD = """Project-Name: GoATee
Description: Spec and delivery control layer.
"""

SAMPLE_CHANGE_MD_TEMPLATE = """# Change: {change_id}

## Intent

Implement feature {change_id}.

## Agreed Scope

### In Scope
- Core functionality

### Out of Scope
- Extra features

## Acceptance Criteria

- AC-01: Automated check passes
  Evidence:
    type: automated
    command: python3 -m unittest
    expected: exit 0

- AC-02: Manual check
  Evidence:
    type: manual
    instruction: Check visually

## Build Budget

### Expected Surface
- src/{change_id}.py

### Reuse Strategy
- Use stdlib

Dependency-Policy: No new runtime dependencies

### Blast Radius
- src/core/

## Contract State

Agreement-Status: {agreement_status}
Budget-Status: {budget_status}
Baseline-Commit: {baseline_commit}
"""

SAMPLE_RECEIPT_MD_TEMPLATE = """# Delivery Receipt: {change_id}

Verdict: {verdict}
Verified-At: 2026-09-20 12:00:00Z

## Commit Provenance

Baseline-Commit: {baseline_commit}
Verified-Commit: {verified_commit}

## Requirement Coverage Matrix

| Criterion | Type | Declared Verification | Observed Result | Status |
| :--- | :---: | :--- | :--- | :---: |
| **AC-01** | automated | `python3 -m unittest` | exit 0 (0.1s) | **VERIFIED** |
| **AC-02** | manual | `Check visually` | Pending human sign-off | **{ac02_status}** |

## Build Budget & Surface Analysis

### Expected Changes
- src/{change_id}.py

### Surface Deviations
- (none)

### Protected Changes (Blast Radius)
- (none)

## Git-Visible Mutation Report

- Zero Git-visible repository mutations detected during verification.

## Diagnostic Excerpt

- No diagnostics recorded (all executed checks clean).
"""


class TestStatus(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        _init_git_repo(self.repo)

        # Initial commit
        (self.repo / "README.md").write_text("# Initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo, check=True, capture_output=True)

        # Create .goatee project
        goatee_dir = self.repo / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text(SAMPLE_PROJECT_MD, encoding="utf-8")
        (goatee_dir / "changes").mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_project_status_overview(self):
        """AC-01: Project overview displays all changes deterministically with separate contract state and machine verdict."""
        # Change 1: 001-feat (verified PASS)
        c1_dir = self.repo / ".goatee" / "changes" / "001-feat"
        c1_dir.mkdir()
        c1_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="001-feat",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit="1111111222222233333333444444445555555566"
        )
        (c1_dir / "change.md").write_text(c1_md, encoding="utf-8")
        c1_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="001-feat",
            verdict="PASS",
            baseline_commit="1111111222222233333333444444445555555566",
            verified_commit="2222222333333344444444555555556666666677",
            ac02_status="VERIFIED"
        )
        (c1_dir / "receipt.md").write_text(c1_rc, encoding="utf-8")

        # Change 2: 002-feat (verified NEEDS_REVIEW)
        c2_dir = self.repo / ".goatee" / "changes" / "002-feat"
        c2_dir.mkdir()
        c2_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="002-feat",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit="1111111222222233333333444444445555555566"
        )
        (c2_dir / "change.md").write_text(c2_md, encoding="utf-8")
        c2_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="002-feat",
            verdict="NEEDS_REVIEW",
            baseline_commit="1111111222222233333333444444445555555566",
            verified_commit="3333333444444455555555666666667777777788",
            ac02_status="NEEDS_REVIEW"
        )
        (c2_dir / "receipt.md").write_text(c2_rc, encoding="utf-8")

        # Change 3: 003-feat (unverified DRAFT)
        c3_dir = self.repo / ".goatee" / "changes" / "003-feat"
        c3_dir.mkdir()
        c3_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="003-feat",
            agreement_status="DRAFT",
            budget_status="DRAFT",
            baseline_commit=""
        )
        (c3_dir / "change.md").write_text(c3_md, encoding="utf-8")

        # Run get_project_status
        report = get_project_status(self.repo)
        self.assertEqual(report.project_name, "GoATee")
        self.assertEqual(report.working_tree, "DIRTY")  # untracked .goatee files
        self.assertEqual(len(report.changes), 3)

        # Check order
        self.assertEqual(report.changes[0].change_id, "001-feat")
        self.assertEqual(report.changes[0].agreement_status, "APPROVED")
        self.assertEqual(report.changes[0].machine_verdict, "PASS")
        self.assertEqual(report.changes[0].commit_label, "Verified")
        self.assertEqual(report.changes[0].coverage_summary, "2/2 VERIFIED")

        self.assertEqual(report.changes[1].change_id, "002-feat")
        self.assertEqual(report.changes[1].agreement_status, "APPROVED")
        self.assertEqual(report.changes[1].machine_verdict, "NEEDS_REVIEW")
        self.assertEqual(report.changes[1].commit_label, "Verified")
        self.assertEqual(report.changes[1].coverage_summary, "1/2 VERIFIED")

        self.assertEqual(report.changes[2].change_id, "003-feat")
        self.assertEqual(report.changes[2].agreement_status, "DRAFT")
        self.assertEqual(report.changes[2].baseline_commit, "(none)")
        self.assertEqual(report.changes[2].machine_verdict, "NOT_VERIFIED")
        self.assertEqual(report.changes[2].commit_label, "Candidate")
        self.assertEqual(report.changes[2].coverage_summary, "PENDING")

        output = format_project_overview(report)
        self.assertIn("Project: GoATee", output)
        self.assertIn("- 001-feat", output)
        self.assertIn("Machine: PASS", output)
        self.assertIn("Coverage: 2/2 VERIFIED", output)
        self.assertIn("- 002-feat", output)
        self.assertIn("Machine: NEEDS_REVIEW", output)
        self.assertIn("Coverage: 1/2 VERIFIED", output)
        self.assertIn("- 003-feat", output)
        self.assertIn("Machine: NOT_VERIFIED", output)
        self.assertIn("Coverage: PENDING", output)

    def test_unverified_change_status_with_candidate_budget(self):
        """AC-02: Approved/baselined change without receipt displays Candidate-Commit, NOT_VERIFIED, and read-only baseline..HEAD budget snapshot."""
        base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()

        # Commit an expected file
        src_dir = self.repo / "src"
        src_dir.mkdir()
        (src_dir / "001-cand.py").write_text("cand = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/001-cand.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add candidate file"], cwd=self.repo, check=True, capture_output=True)
        cand_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()

        # Create approved change contract with base_sha
        c_dir = self.repo / ".goatee" / "changes" / "001-cand"
        c_dir.mkdir(parents=True)
        c_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="001-cand",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit=base_sha
        )
        (c_dir / "change.md").write_text(c_md, encoding="utf-8")

        contract = parse_change_contract(c_dir / "change.md")
        report = get_change_report(self.repo, "001-cand", "CLEAN", cand_sha)

        self.assertFalse(report.is_verified)
        self.assertEqual(report.candidate_commit, cand_sha)
        self.assertEqual(report.machine_verdict, "NOT_VERIFIED")
        self.assertEqual(report.budget_snapshot_label, "committed candidate only")
        self.assertIn("src/001-cand.py", report.expected_changes)
        self.assertEqual(report.surface_deviations, [])
        self.assertEqual(report.protected_changes, [])

        output = format_change_detail(report, contract)
        self.assertIn("Candidate-Commit: " + cand_sha, output)
        self.assertIn("Machine Verdict: NOT_VERIFIED", output)
        self.assertIn("Build Budget Snapshot: (committed candidate only)", output)
        self.assertIn("Expected Changes: 1", output)
        self.assertIn("- src/001-cand.py", output)
        self.assertIn("Surface Deviations: (none)", output)
        self.assertIn("Protected Changes: (none)", output)

    def test_unbaselined_change_status(self):
        """AC-02: Draft change with unpopulated baseline displays UNAVAILABLE — baseline not locked."""
        c_dir = self.repo / ".goatee" / "changes" / "001-draft"
        c_dir.mkdir(parents=True)
        c_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="001-draft",
            agreement_status="DRAFT",
            budget_status="DRAFT",
            baseline_commit=""
        )
        (c_dir / "change.md").write_text(c_md, encoding="utf-8")

        contract = parse_change_contract(c_dir / "change.md")
        report = get_change_report(self.repo, "001-draft", "CLEAN", "dummyhead")

        self.assertEqual(report.budget_snapshot_label, "UNAVAILABLE — baseline not locked")
        self.assertEqual(report.expected_changes, [])
        output = format_change_detail(report, contract)
        self.assertIn("Build Budget Snapshot:\nUNAVAILABLE — baseline not locked", output)

    def test_verified_change_status_from_receipt(self):
        """AC-03: Verified change reads receipt snapshot and displays Verified-Commit, Verdict, coverage, and stored surface analysis."""
        base_sha = "1111111222222233333333444444445555555566"
        ver_sha = "2222222333333344444444555555556666666677"

        c_dir = self.repo / ".goatee" / "changes" / "001-ver"
        c_dir.mkdir(parents=True)
        c_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="001-ver",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit=base_sha
        )
        (c_dir / "change.md").write_text(c_md, encoding="utf-8")
        c_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="001-ver",
            verdict="PASS",
            baseline_commit=base_sha,
            verified_commit=ver_sha,
            ac02_status="VERIFIED"
        )
        (c_dir / "receipt.md").write_text(c_rc, encoding="utf-8")

        # Even if current HEAD has moved to another commit
        current_head = "9999999999999999999999999999999999999999"
        report = get_change_report(self.repo, "001-ver", "CLEAN", current_head)

        self.assertTrue(report.is_verified)
        self.assertEqual(report.verified_commit, ver_sha)  # Not current_head!
        self.assertEqual(report.machine_verdict, "PASS")
        self.assertEqual(report.verified_at, "2026-09-20 12:00:00Z")
        self.assertIn("src/001-ver.py", report.expected_changes)

        contract = parse_change_contract(c_dir / "change.md")
        output = format_change_detail(report, contract)
        self.assertIn("Evidence State: (receipt snapshot)", output)
        self.assertIn("Verified-Commit: " + ver_sha, output)
        self.assertIn("Verified-At: 2026-09-20 12:00:00Z", output)
        self.assertIn("Machine Verdict: PASS", output)
        self.assertIn("Human Review: PENDING", output)
        self.assertIn("- AC-01: [automated]\n  Machine Status:    VERIFIED", output)
        self.assertIn("- AC-02: [manual]\n  Machine Status:    VERIFIED\n  Human Attestation: PENDING", output)

        # Provenance test: modify contract description after verification
        c_md_modified = c_md.replace(
            "- AC-01: Automated check passes",
            "- AC-01: Changed description after verification"
        )
        (c_dir / "change.md").write_text(c_md_modified, encoding="utf-8")

        contract_modified = parse_change_contract(c_dir / "change.md")
        report_modified = get_change_report(self.repo, "001-ver", "CLEAN", current_head)
        output_modified = format_change_detail(report_modified, contract_modified)

        self.assertTrue(report_modified.is_verified)
        self.assertEqual(report_modified.machine_verdict, "PASS")
        self.assertIn("- AC-01: [automated]\n  Machine Status:    VERIFIED", output_modified)
        # Contract's modified description must NOT leak into the historical receipt snapshot
        self.assertNotIn("Changed description after verification", output_modified)

    def test_detailed_unverified_criteria_pending(self):
        """AC-04: Detailed unverified change displays contract/build-budget metadata and criteria as PENDING_VERIFICATION."""
        c_dir = self.repo / ".goatee" / "changes" / "001-pend"
        c_dir.mkdir(parents=True)
        c_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="001-pend",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit=""
        )
        (c_dir / "change.md").write_text(c_md, encoding="utf-8")

        contract = parse_change_contract(c_dir / "change.md")
        report = get_change_report(self.repo, "001-pend", "CLEAN", "headsha")
        output = format_change_detail(report, contract)

        self.assertIn("Acceptance Criteria:", output)
        self.assertIn("- AC-01: [automated] PENDING_VERIFICATION", output)
        self.assertIn("- AC-02: [manual] PENDING_VERIFICATION", output)

    def test_malformed_artifacts_fail_closed(self):
        """AC-06: Malformed change.md, malformed receipt.md, incomplete change directories, contract-receipt identity/baseline/criterion mismatches, and malformed project metadata fail closed with concise diagnostics."""
        # 1. Malformed project metadata (missing Project-Name)
        (self.repo / ".goatee" / "project.md").write_text("Description: no name\n", encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            get_project_status(self.repo)
        self.assertIn("missing Project-Name", str(ctx.exception))

        # Restore project.md
        (self.repo / ".goatee" / "project.md").write_text(SAMPLE_PROJECT_MD, encoding="utf-8")

        # 2. Incomplete change directory (missing change.md)
        incomp_dir = self.repo / ".goatee" / "changes" / "999-incomplete"
        incomp_dir.mkdir()
        with self.assertRaises(ControlStateError) as ctx:
            get_project_status(self.repo)
        self.assertIn("missing change.md", str(ctx.exception))
        incomp_dir.rmdir()

        # Setup valid base contract for subsequent consistency checks
        c_dir = self.repo / ".goatee" / "changes" / "001-test"
        c_dir.mkdir()
        base_contract_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id="001-test",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit="1111111222222233333333444444445555555566"
        )
        (c_dir / "change.md").write_text(base_contract_md, encoding="utf-8")

        # 3. Malformed change.md (ContractSyntaxError wrapped as ControlStateError)
        (c_dir / "change.md").write_text("not a valid contract", encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            get_change_report(self.repo, "001-test", "CLEAN", "headsha")
        self.assertIn("Malformed contract", str(ctx.exception))

        # Restore valid change.md
        (c_dir / "change.md").write_text(base_contract_md, encoding="utf-8")

        # 4. Malformed receipt.md (unrecognized Verdict)
        bad_verdict_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="001-test",
            verdict="FAILED",  # FAILED is not an accepted receipt verdict
            baseline_commit="1111111222222233333333444444445555555566",
            verified_commit="2222222333333344444444555555556666666677",
            ac02_status="VERIFIED"
        )
        (c_dir / "receipt.md").write_text(bad_verdict_rc, encoding="utf-8")
        with self.assertRaises(ReceiptSyntaxError) as ctx:
            get_change_report(self.repo, "001-test", "CLEAN", "headsha")
        self.assertIn("unrecognized Verdict 'FAILED'", str(ctx.exception))

        # 5. Malformed receipt.md (unrecognized criterion status)
        bad_status_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="001-test",
            verdict="PASS",
            baseline_commit="1111111222222233333333444444445555555566",
            verified_commit="2222222333333344444444555555556666666677",
            ac02_status="UNKNOWN_STATUS"
        )
        (c_dir / "receipt.md").write_text(bad_status_rc, encoding="utf-8")
        with self.assertRaises(ReceiptSyntaxError) as ctx:
            get_change_report(self.repo, "001-test", "CLEAN", "headsha")
        self.assertIn("unrecognized criterion status 'UNKNOWN_STATUS'", str(ctx.exception))

        # 6. Contract <-> Receipt change ID mismatch
        id_mismatch_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="wrong-id",
            verdict="PASS",
            baseline_commit="1111111222222233333333444444445555555566",
            verified_commit="2222222333333344444444555555556666666677",
            ac02_status="VERIFIED"
        )
        (c_dir / "receipt.md").write_text(id_mismatch_rc, encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            get_change_report(self.repo, "001-test", "CLEAN", "headsha")
        self.assertIn("receipt change ID 'wrong-id' does not match contract", str(ctx.exception))

        # 7. Contract <-> Receipt baseline mismatch
        base_mismatch_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id="001-test",
            verdict="PASS",
            baseline_commit="9999999999999999999999999999999999999999",
            verified_commit="2222222333333344444444555555556666666677",
            ac02_status="VERIFIED"
        )
        (c_dir / "receipt.md").write_text(base_mismatch_rc, encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            get_change_report(self.repo, "001-test", "CLEAN", "headsha")
        self.assertIn("receipt Baseline-Commit does not match contract Baseline-Commit", str(ctx.exception))

        # 8. Contract <-> Receipt criterion ID mismatch (missing AC-02)
        missing_ac_rc = """# Delivery Receipt: 001-test

Verdict: PASS
Verified-At: 2026-09-20 12:00:00Z

## Commit Provenance

Baseline-Commit: 1111111222222233333333444444445555555566
Verified-Commit: 2222222333333344444444555555556666666677

## Requirement Coverage Matrix

| Criterion | Type | Declared Verification | Observed Result | Status |
| :--- | :---: | :--- | :--- | :---: |
| **AC-01** | automated | `python3 -m unittest` | exit 0 (0.1s) | **VERIFIED** |

## Build Budget & Surface Analysis

### Expected Changes
- src/001-test.py

### Surface Deviations
- (none)

### Protected Changes (Blast Radius)
- (none)
"""
        (c_dir / "receipt.md").write_text(missing_ac_rc, encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            get_change_report(self.repo, "001-test", "CLEAN", "headsha")
        self.assertIn("receipt criteria set does not match contract criteria", str(ctx.exception))

    def test_dual_verdict_status_states(self):
        """AC-08: Dual-verdict status reporting correctly handles no-review, valid committed review, uncommitted review, and stale review."""
        from goatee.core.attest import record_attestation

        # Setup initial commit and change
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: setup repo for dual-verdict"], cwd=self.repo, check=True)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        change_id = "008-dual"
        c_dir = self.repo / ".goatee" / "changes" / change_id
        c_dir.mkdir(parents=True, exist_ok=True)
        c_md = SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id=change_id,
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit=base_sha
        )
        (c_dir / "change.md").write_text(c_md, encoding="utf-8")

        # Candidate commit
        src_file = self.repo / "src" / f"{change_id}.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# candidate\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"feat: candidate {change_id}"], cwd=self.repo, check=True)
        cand_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Receipt with NEEDS_REVIEW
        c_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id=change_id,
            verdict="NEEDS_REVIEW",
            baseline_commit=base_sha,
            verified_commit=cand_sha,
            ac01_status="VERIFIED",
            ac02_status="NEEDS_REVIEW"
        )
        receipt_path = c_dir / "receipt.md"
        receipt_path.write_text(c_rc, encoding="utf-8")
        subprocess.run(["git", "add", str(receipt_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"chore: receipt {change_id}"], cwd=self.repo, check=True)
        rec_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # 1. State A: No review.md -> Human Review: PENDING
        report_a = get_change_report(self.repo, change_id, "CLEAN", rec_sha)
        self.assertEqual(report_a.machine_verdict, "NEEDS_REVIEW")
        self.assertEqual(report_a.human_review, "PENDING")
        self.assertIsNone(report_a.acceptance_blocked_reason)
        out_a = format_change_detail(report_a)
        self.assertIn("Machine Verdict: NEEDS_REVIEW", out_a)
        self.assertIn("Human Review: PENDING", out_a)
        self.assertIn("- AC-01: [automated]\n  Machine Status:    VERIFIED", out_a)
        self.assertIn("- AC-02: [manual]\n  Machine Status:    NEEDS_REVIEW\n  Human Attestation: PENDING", out_a)

        proj_a = get_project_status(self.repo)
        out_proj_a = format_project_overview(proj_a)
        self.assertIn("Machine: NEEDS_REVIEW", out_proj_a)
        self.assertIn("Human: PENDING", out_proj_a)

        # 2. State B: Valid committed review.md -> Human Review: ACCEPTED
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Manual verification passed.",
        )
        subprocess.run(["git", "add", str(c_dir / "review.md")], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit review"], cwd=self.repo, check=True)
        rev_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        report_b = get_change_report(self.repo, change_id, "CLEAN", rev_head)
        self.assertEqual(report_b.machine_verdict, "NEEDS_REVIEW")
        self.assertEqual(report_b.human_review, "ACCEPTED")
        self.assertIsNotNone(report_b.review_updated_at)
        self.assertIn("GoATee Test", report_b.latest_reviewer)
        out_b = format_change_detail(report_b)
        self.assertIn("Human Review: ACCEPTED", out_b)
        self.assertIn("Latest Reviewer: GoATee Test <test@goatee.dev> (Git-configured attribution)", out_b)
        self.assertIn("- AC-02: [manual]\n  Machine Status:    NEEDS_REVIEW\n  Human Attestation: VERIFIED (attested by GoATee Test <test@goatee.dev> (Git-configured attribution))\n  Note: Manual verification passed.", out_b)

        # 3. State C: Uncommitted / modified review.md -> Fails closed
        (c_dir / "review.md").write_text((c_dir / "review.md").read_text() + " ", encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx_c:
            get_change_report(self.repo, change_id, "DIRTY", rev_head)
        self.assertIn("uncommitted changes", str(ctx_c.exception))

        # Revert modification
        subprocess.run(["git", "checkout", "--", str(c_dir / "review.md")], cwd=self.repo, check=True)

        # 4. State D: Stale review -> Fails closed
        receipt_path.write_text(c_rc.replace("2026-09-20 12:00:00Z", "2026-09-20 14:00:00Z"), encoding="utf-8")
        subprocess.run(["git", "add", str(receipt_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: update receipt"], cwd=self.repo, check=True)
        with self.assertRaises(ControlStateError) as ctx_d:
            get_change_report(self.repo, change_id, "CLEAN", rev_head)
        self.assertIn("stale", str(ctx_d.exception).lower())

        # 5. State E: Flawed machine proof with no human rejection -> Human Review: PENDING with blocked reason
        change_id_flawed = "008-flawed"
        c_dir_flawed = self.repo / ".goatee" / "changes" / change_id_flawed
        c_dir_flawed.mkdir(parents=True, exist_ok=True)
        (c_dir_flawed / "change.md").write_text(SAMPLE_CHANGE_MD_TEMPLATE.format(
            change_id=change_id_flawed,
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit=base_sha
        ), encoding="utf-8")

        flawed_rc = SAMPLE_RECEIPT_MD_TEMPLATE.format(
            change_id=change_id_flawed,
            verdict="NEEDS_REVIEW",
            baseline_commit=base_sha,
            verified_commit=cand_sha,
            ac02_status="NEEDS_REVIEW"
        ).replace("**VERIFIED**", "**FAILED**", 1)
        (c_dir_flawed / "receipt.md").write_text(flawed_rc, encoding="utf-8")
        subprocess.run(["git", "add", str(c_dir_flawed)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"chore: flawed receipt {change_id_flawed}"], cwd=self.repo, check=True)

        report_e = get_change_report(self.repo, change_id_flawed, "CLEAN", cand_sha)
        self.assertEqual(report_e.machine_verdict, "NEEDS_REVIEW")
        self.assertEqual(report_e.human_review, "PENDING")
        self.assertEqual(report_e.acceptance_blocked_reason, "Acceptance blocked by machine evidence.")
        out_e = format_change_detail(report_e)
        self.assertIn("Human Review: PENDING", out_e)
        self.assertIn("(Acceptance blocked by machine evidence.)", out_e)

    def test_parse_receipt_escaped_pipes(self):
        """Receipt parser must cleanly handle cells containing escaped pipe characters."""
        receipt_md = """# Delivery Receipt: 001-pipe

Verdict: PASS
Verified-At: 2026-11-17 12:00:00Z

## Commit Provenance

Baseline-Commit: 1234567890abcdef
Verified-Commit: abcdef1234567890

## Requirement Coverage Matrix

| Criterion | Type | Declared Verification | Observed Result | Status |
| :--- | :---: | :--- | :--- | :---: |
| **AC-01** | automated | `cat file \\| grep foo` | output with \\| pipe | **VERIFIED** |

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
        with tempfile.TemporaryDirectory() as tmp:
            r_path = Path(tmp) / "receipt.md"
            r_path.write_text(receipt_md, encoding="utf-8")
            snapshot = parse_receipt(r_path)
            self.assertEqual(snapshot.verdict, "PASS")
            self.assertEqual(len(snapshot.rows), 1)
            self.assertEqual(snapshot.rows[0].criterion_id, "AC-01")
            self.assertEqual(snapshot.rows[0].declared, "`cat file | grep foo`")
            self.assertEqual(snapshot.rows[0].observed, "output with | pipe")
            self.assertEqual(snapshot.rows[0].status, "VERIFIED")


if __name__ == "__main__":
    unittest.main()
