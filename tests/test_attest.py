"""Unit and integration tests for GoATee human attestation and review management."""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from goatee.core.attest import (
    ReviewEvent,
    ReviewRecord,
    derive_review_state,
    escape_table_cell,
    format_review_markdown,
    load_and_validate_review,
    normalize_note,
    parse_decision_argument,
    parse_review_markdown,
    record_attestation,
    record_multi_attestation,
    run_interactive_attestation,
)
from unittest.mock import patch
from goatee.core.parser import parse_change_contract
from goatee.core.status import ControlStateError, parse_receipt


def _init_repo(path: Path) -> None:
    """Initialize a git repository with configured user identity."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@goatee.dev"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "GoATee Tester"], cwd=path, check=True)


SAMPLE_PROJECT = """Project-Name: GoATeeTest
Description: Test project for GoATee.
"""

SAMPLE_CONTRACT_TEMPLATE = """# Change: {change_id}

## Intent

Implement feature {change_id}.

## Agreed Scope

### In Scope
- Core code

### Out of Scope
- Extra items

## Acceptance Criteria

- AC-01: Automated test passes
  Evidence:
    type: automated
    command: python3 -c 'exit(0)'
    expected: exit 0

- AC-02: Manual dependency check
  Evidence:
    type: manual
    instruction: Check pyproject.toml dependencies

## Build Budget

### Expected Surface
- src/{change_id}.py

### Reuse Strategy
- stdlib only

Dependency-Policy: No new runtime dependencies

### Blast Radius
- src/core/

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: {baseline_commit}
"""

SAMPLE_RECEIPT_TEMPLATE = """# Delivery Receipt: {change_id}

Verdict: {verdict}
Verified-At: 2026-09-21 00:00:00Z

## Commit Provenance

Baseline-Commit: {baseline_commit}
Verified-Commit: {verified_commit}

## Requirement Coverage Matrix

| Criterion | Type | Declared Verification | Observed Result | Status |
| :--- | :---: | :--- | :--- | :---: |
| **AC-01** | automated | `python3 -c 'exit(0)'` | exit 0 (0.01s) | **{ac01_status}** |
| **AC-02** | manual | `Check pyproject.toml dependencies` | Pending human observation and sign-off | **{ac02_status}** |

## Build Budget & Surface Analysis

### Expected Changes
- src/{change_id}.py

### Surface Deviations
- (none)

### Protected Changes
- (none)

## Git-Visible Mutation Report

- Zero Git-visible repository mutations detected during verification.

## Diagnostic Excerpt

- No diagnostics recorded (all executed checks clean).
"""


class TestAttest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        _init_repo(self.repo)

        # Initial commit
        (self.repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=self.repo, check=True)
        self.base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Initialize .goatee
        goatee_dir = self.repo / ".goatee"
        goatee_dir.mkdir(parents=True)
        (goatee_dir / "project.md").write_text(SAMPLE_PROJECT, encoding="utf-8")
        (goatee_dir / "changes").mkdir()
        subprocess.run(["git", "add", ".goatee"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: init goatee"], cwd=self.repo, check=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _setup_verified_change(
        self,
        change_id: str,
        verdict: str = "NEEDS_REVIEW",
        ac01_status: str = "VERIFIED",
        ac02_status: str = "NEEDS_REVIEW"
    ) -> tuple[Path, str, str]:
        """Helper to create and commit a verified change with receipt."""
        change_dir = self.repo / ".goatee" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)

        contract_text = SAMPLE_CONTRACT_TEMPLATE.format(
            change_id=change_id,
            baseline_commit=self.base_commit,
        )
        (change_dir / "change.md").write_text(contract_text, encoding="utf-8")

        # Create candidate file
        src_dir = self.repo / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / f"{change_id}.py").write_text("# impl\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"feat: {change_id}"], cwd=self.repo, check=True)
        verified_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Create receipt
        receipt_text = SAMPLE_RECEIPT_TEMPLATE.format(
            change_id=change_id,
            verdict=verdict,
            baseline_commit=self.base_commit,
            verified_commit=verified_commit,
            ac01_status=ac01_status,
            ac02_status=ac02_status,
        )
        receipt_path = change_dir / "receipt.md"
        receipt_path.write_text(receipt_text, encoding="utf-8")

        subprocess.run(["git", "add", str(receipt_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"chore: record receipt for {change_id}"], cwd=self.repo, check=True)
        receipt_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        return change_dir, verified_commit, receipt_commit

    def test_attest_manual_criterion_success(self):
        """AC-01: Positive attestation of manual criterion creates/updates review.md with deterministic event record and derived projections."""
        change_id = "001-attest"
        change_dir, ver_commit, rec_commit = self._setup_verified_change(change_id)

        # Attest AC-02 as verified
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Inspected pyproject.toml, zero external runtime dependencies.",
        )

        review_path = change_dir / "review.md"
        self.assertTrue(review_path.is_file())

        review = parse_review_markdown(review_path)
        self.assertEqual(review.change_id, change_id)
        self.assertEqual(review.verdict, "ACCEPTED")
        self.assertEqual(review.verified_commit, ver_commit)
        self.assertEqual(review.receipt_commit, rec_commit)

        # Check coverage matrix
        self.assertEqual(len(review.rows), 1)
        row = review.rows[0]
        self.assertEqual(row.criterion_id, "AC-02")
        self.assertEqual(row.criterion_type, "manual")
        self.assertEqual(row.machine_status, "NEEDS_REVIEW")
        self.assertEqual(row.human_attestation, "VERIFIED")
        self.assertIn("GoATee Tester", row.reviewer)
        self.assertEqual(row.note, "Inspected pyproject.toml, zero external runtime dependencies.")

        # Check review events
        self.assertEqual(len(review.events), 1)
        ev = review.events[0]
        self.assertEqual(ev.sequence, 1)
        self.assertEqual(ev.scope, "criterion")
        self.assertEqual(ev.criterion_id, "AC-02")
        self.assertEqual(ev.observation, "VERIFIED")
        self.assertEqual(ev.reviewer_name, "GoATee Tester")
        self.assertEqual(ev.reviewer_email, "tester@goatee.dev")
        self.assertEqual(ev.attribution, "Git-configured")

    def test_receipt_binding_and_tamper_detection(self):
        """AC-02: Receipt binding strictly validates Verified-Commit, Receipt-Commit, and Receipt-SHA256, failing closed on tamper or mismatch."""
        change_id = "002-tamper"
        change_dir, ver_commit, rec_commit = self._setup_verified_change(change_id)

        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Verified visually.",
        )
        review_path = change_dir / "review.md"
        subprocess.run(["git", "add", str(review_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit review"], cwd=self.repo, check=True)

        contract = parse_change_contract(change_dir / "change.md")
        receipt = parse_receipt(change_dir / "receipt.md")

        # Valid load succeeds
        loaded = load_and_validate_review(change_dir, contract, receipt, self.repo)
        self.assertIsNotNone(loaded)

        # Tamper receipt bytes
        receipt_path = change_dir / "receipt.md"
        receipt_path.write_text(receipt_path.read_text() + "\n# Extra line\n", encoding="utf-8")

        with self.assertRaises(ControlStateError) as ctx:
            load_and_validate_review(change_dir, contract, receipt, self.repo)
        self.assertIn("stale", str(ctx.exception).lower())

    def test_stale_review_fails_closed(self):
        """AC-03: Stale review detection fails closed when receipt is updated or re-verified without silent rebinding."""
        change_id = "003-stale"
        change_dir, ver_commit, rec_commit = self._setup_verified_change(change_id)

        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Verified visually.",
        )
        subprocess.run(["git", "add", str(change_dir / "review.md")], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit review"], cwd=self.repo, check=True)

        # Re-verify or alter receipt and commit it
        receipt_path = change_dir / "receipt.md"
        modified_receipt = receipt_path.read_text().replace("2026-09-21 00:00:00Z", "2026-09-21 01:00:00Z")
        receipt_path.write_text(modified_receipt, encoding="utf-8")
        subprocess.run(["git", "add", str(receipt_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: update receipt"], cwd=self.repo, check=True)

        contract = parse_change_contract(change_dir / "change.md")
        receipt = parse_receipt(receipt_path)

        with self.assertRaises(ControlStateError) as ctx:
            load_and_validate_review(change_dir, contract, receipt, self.repo)
        self.assertIn("stale", str(ctx.exception).lower())

    def test_positive_attestation_eligibility(self):
        """AC-04: Positive attestation eligibility strictly rejects --verified for automated criteria, manual criteria not in NEEDS_REVIEW, type mismatches, and instruction drift."""
        change_id = "004-elig"
        change_dir, ver_commit, rec_commit = self._setup_verified_change(
            change_id, ac01_status="VERIFIED", ac02_status="NEEDS_REVIEW"
        )

        # 1. Attempt --verified on automated AC-01 -> fails
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id,
                criterion_id="AC-01",
                verified=True,
                rejected=False,
                note="Attempting to override machine automated check",
            )
        self.assertIn("declared as automated", str(ctx.exception))

        # 2. Attempt --verified on manual criterion whose status is NOT_RUN
        change_id_notrun = "004-notrun"
        self._setup_verified_change(
            change_id_notrun, ac01_status="FAILED", ac02_status="NOT_RUN"
        )
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id_notrun,
                criterion_id="AC-02",
                verified=True,
                rejected=False,
                note="Attempting to attest NOT_RUN manual criterion",
            )
        self.assertIn("cannot be positively attested because its machine status is NOT_RUN", str(ctx.exception))

        # 3. Attempt --verified on manual criterion whose status is FAILED
        change_id_failed = "004-failed"
        self._setup_verified_change(
            change_id_failed, ac01_status="VERIFIED", ac02_status="FAILED"
        )
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id_failed,
                criterion_id="AC-02",
                verified=True,
                rejected=False,
                note="Attempting to attest FAILED manual criterion",
            )
        self.assertIn("cannot be positively attested because its machine status is FAILED", str(ctx.exception))

        # Subcase A: contract says manual, receipt says automated -> fails closed
        change_id_sub_a = "004-sub-a"
        dir_a, _, _ = self._setup_verified_change(change_id_sub_a)
        # Edit receipt to say automated for AC-02
        rec_path_a = dir_a / "receipt.md"
        rec_text_a = rec_path_a.read_text(encoding="utf-8").replace(
            "| **AC-02** | manual |", "| **AC-02** | automated |"
        )
        rec_path_a.write_text(rec_text_a, encoding="utf-8")
        subprocess.run(["git", "add", str(rec_path_a)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "receipt with AC-02 automated"], cwd=self.repo, check=True)
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id_sub_a,
                criterion_id="AC-02",
                verified=True,
                rejected=False,
                note="Attesting AC-02 with type mismatch",
            )
        self.assertIn("Error: Criterion 'AC-02' type differs between change contract and delivery receipt. Review cannot continue against inconsistent evidence.", str(ctx.exception))

        # Subcase B: contract says automated, receipt says manual -> fails closed
        change_id_sub_b = "004-sub-b"
        dir_b, _, _ = self._setup_verified_change(change_id_sub_b)
        # Edit receipt to say manual for AC-01
        rec_path_b = dir_b / "receipt.md"
        rec_text_b = rec_path_b.read_text(encoding="utf-8").replace(
            "| **AC-01** | automated |", "| **AC-01** | manual |"
        )
        rec_path_b.write_text(rec_text_b, encoding="utf-8")
        subprocess.run(["git", "add", str(rec_path_b)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "receipt with AC-01 manual"], cwd=self.repo, check=True)
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id_sub_b,
                criterion_id="AC-01",
                verified=True,
                rejected=False,
                note="Attesting AC-01 with type mismatch",
            )
        self.assertIn("Error: Criterion 'AC-01' type differs between change contract and delivery receipt. Review cannot continue against inconsistent evidence.", str(ctx.exception))

        # Subcase C: both say manual but current contract instruction differs from receipt Declared Verification -> fails closed
        change_id_sub_c = "004-sub-c"
        dir_c, _, _ = self._setup_verified_change(change_id_sub_c)
        c_path_c = dir_c / "change.md"
        c_text_c = c_path_c.read_text(encoding="utf-8").replace(
            "instruction: Check pyproject.toml dependencies",
            "instruction: Check pyproject.toml for changed package hashes"
        )
        c_path_c.write_text(c_text_c, encoding="utf-8")
        subprocess.run(["git", "add", str(c_path_c)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "change contract with drifted instruction"], cwd=self.repo, check=True)
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id_sub_c,
                criterion_id="AC-02",
                verified=True,
                rejected=False,
                note="Attesting AC-02 after instruction drift",
            )
        self.assertIn("Error: Criterion 'AC-02' manual evidence instruction differs from the verified receipt. Human attestation cannot be applied to changed evidence requirements.", str(ctx.exception))

        # Subcase D: both say manual, instruction matches, receipt Machine Status == NEEDS_REVIEW -> positive attestation succeeds
        change_id_sub_d = "004-sub-d"
        dir_d, _, _ = self._setup_verified_change(change_id_sub_d)
        record_attestation(
            repo_root=self.repo,
            change_id=change_id_sub_d,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Manual instruction checked and confirmed matching.",
        )
        rev_d = parse_review_markdown(dir_d / "review.md")
        self.assertEqual(rev_d.verdict, "ACCEPTED")
        ac02_d_row = next(r for r in rev_d.rows if r.criterion_id == "AC-02")
        self.assertEqual(ac02_d_row.human_attestation, "VERIFIED")
        self.assertEqual(ac02_d_row.machine_status, "NEEDS_REVIEW")

    def test_rejection_supersession_and_stickiness(self):
        """AC-05: Rejection semantics enforce manual supersession, automated stickiness, and change-level stickiness."""
        change_id = "005-reject"
        change_dir, _, _ = self._setup_verified_change(change_id)

        # 1. Reject manual AC-02
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=False,
            rejected=True,
            note="Manual criterion initially rejected.",
        )
        review = parse_review_markdown(change_dir / "review.md")
        self.assertEqual(review.verdict, "REJECTED")
        self.assertEqual(review.rows[0].human_attestation, "REJECTED")

        # Commit review before second attest
        subprocess.run(["git", "add", str(change_dir / "review.md")], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit review rejection"], cwd=self.repo, check=True)

        # 2. Supersede manual AC-02 with VERIFIED
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Re-reviewed manual criterion, now verified.",
        )
        review2 = parse_review_markdown(change_dir / "review.md")
        self.assertEqual(review2.verdict, "ACCEPTED")
        self.assertEqual(review2.rows[0].human_attestation, "VERIFIED")
        self.assertEqual(len(review2.events), 2)

        subprocess.run(["git", "add", str(change_dir / "review.md")], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit review acceptance"], cwd=self.repo, check=True)

        # 3. Reject automated AC-01 -> sticky for receipt
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-01",
            verified=False,
            rejected=True,
            note="Human disagrees with automated test coverage.",
        )
        review3 = parse_review_markdown(change_dir / "review.md")
        self.assertEqual(review3.verdict, "REJECTED")
        # Attestation matrix now has 2 rows: AC-02 manual and AC-01 automated rejection
        self.assertEqual(len(review3.rows), 2)
        ac01_row = next(r for r in review3.rows if r.criterion_id == "AC-01")
        self.assertEqual(ac01_row.human_attestation, "REJECTED")
        self.assertEqual(ac01_row.machine_status, "VERIFIED")

        # 4. Change-level rejection
        change_id_chg = "005-chgreject"
        change_dir_chg, _, _ = self._setup_verified_change(change_id_chg)
        record_attestation(
            repo_root=self.repo,
            change_id=change_id_chg,
            criterion_id=None,
            verified=False,
            rejected=True,
            note="Overall change rejected by release engineering.",
        )
        rev_chg = parse_review_markdown(change_dir_chg / "review.md")
        self.assertEqual(rev_chg.verdict, "REJECTED")
        self.assertEqual(rev_chg.events[0].scope, "change")

    def test_event_history_sequence_preservation_and_projections(self):
        """AC-06: Event history sequence order, byte-identical append preservation, and projection reconciliation."""
        change_id = "006-seq"
        change_dir, _, _ = self._setup_verified_change(change_id)

        # Attestation 1
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=False,
            rejected=True,
            note="First observation: rejected.",
        )
        review_path = change_dir / "review.md"
        content1 = review_path.read_text(encoding="utf-8")

        # Capture Event: 0001 block
        m1 = re.search(r"(- Event: 0001.*?\n  Note: First observation: rejected\.)", content1, re.DOTALL)
        self.assertIsNotNone(m1)
        event1_bytes = m1.group(1).encode("utf-8")

        subprocess.run(["git", "add", str(review_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: commit event 0001"], cwd=self.repo, check=True)

        # Attestation 2
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Second observation: verified.",
        )
        content2 = review_path.read_text(encoding="utf-8")

        # Verify Event 0001 bytes remain byte-identical
        m2 = re.search(r"(- Event: 0001.*?\n  Note: First observation: rejected\.)", content2, re.DOTALL)
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1).encode("utf-8"), event1_bytes)

        # Verify Event 0002 is appended in sequence
        self.assertIn("- Event: 0002", content2)
        self.assertIn("Note: Second observation: verified.", content2)

        # Reconcile projection: manual tamper of Review-Verdict must fail closed
        tampered_content = content2.replace("Review-Verdict: ACCEPTED", "Review-Verdict: REJECTED")
        review_path.write_text(tampered_content, encoding="utf-8")
        subprocess.run(["git", "add", str(review_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "tamper review"], cwd=self.repo, check=True)

        contract = parse_change_contract(change_dir / "change.md")
        receipt = parse_receipt(change_dir / "receipt.md")
        with self.assertRaises(ControlStateError) as ctx:
            load_and_validate_review(change_dir, contract, receipt, self.repo)
        self.assertIn("projection mismatch", str(ctx.exception).lower())

    def test_clean_worktree_prerequisite_and_zero_side_effects(self):
        """AC-07: Clean worktree requirement before attest and proof that goatee attest mutates ONLY review.md."""
        change_id = "007-clean"
        change_dir, ver_commit, rec_commit = self._setup_verified_change(change_id)

        # Create a dirty file in worktree
        dirty_file = self.repo / "dirty.txt"
        dirty_file.write_text("dirty content", encoding="utf-8")

        # Must fail closed on dirty worktree
        with self.assertRaises(ControlStateError) as ctx:
            record_attestation(
                repo_root=self.repo,
                change_id=change_id,
                criterion_id="AC-02",
                verified=True,
                rejected=False,
                note="Should fail",
            )
        self.assertIn("Working tree must be clean", str(ctx.exception))

        # Remove dirty file and capture fingerprints
        dirty_file.unlink()

        # Capture state before attest
        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        index_diff_before = subprocess.run(
            ["git", "diff", "--cached"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout
        receipt_bytes_before = (change_dir / "receipt.md").read_bytes()
        change_bytes_before = (change_dir / "change.md").read_bytes()
        project_bytes_before = (self.repo / ".goatee" / "project.md").read_bytes()

        # Run attest
        record_attestation(
            repo_root=self.repo,
            change_id=change_id,
            criterion_id="AC-02",
            verified=True,
            rejected=False,
            note="Clean worktree verified.",
        )

        # Verify mutation boundary: only review.md was created
        status_lines = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.splitlines()

        self.assertEqual(len(status_lines), 1)
        self.assertTrue(status_lines[0].endswith(".goatee/changes/007-clean/review.md"))

        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        index_diff_after = subprocess.run(
            ["git", "diff", "--cached"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout

        self.assertEqual(head_before, head_after)
        self.assertEqual(index_diff_before, index_diff_after)
        self.assertEqual((change_dir / "receipt.md").read_bytes(), receipt_bytes_before)
        self.assertEqual((change_dir / "change.md").read_bytes(), change_bytes_before)
        self.assertEqual((self.repo / ".goatee" / "project.md").read_bytes(), project_bytes_before)

    def test_parse_review_escaped_pipe_in_note(self):
        """Review table parser must cleanly parse rows containing escaped pipe characters."""
        review_md = """# Human Review: 001-pipe

Change-ID: 001-pipe
Review-Verdict: ACCEPTED
Updated-At: 2026-11-17 12:00:00Z

## Receipt Binding

Verified-Commit: abc123def456
Receipt-Commit: def456abc123
Receipt-SHA256: 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef

## Attestation Coverage Matrix

| Criterion | Type | Machine Status | Human Attestation | Reviewer | Attested At | Note |
| :--- | :---: | :---: | :---: | :--- | :--- | :--- |
| **AC-01** | manual | NEEDS_REVIEW | **VERIFIED** | Alice <alice@test.dev> | 2026-11-17 12:00:00Z | Note with a \\| pipe character |

## Review Event History

- Event: 0001
  Attested-At: 2026-11-17 12:00:00Z
  Scope: criterion
  Criterion: AC-01
  Observation: VERIFIED
  Reviewer-Name: Alice
  Reviewer-Email: alice@test.dev
  Attribution: Git-configured
  Note: Note with a | pipe character
"""
        with tempfile.TemporaryDirectory() as tmp:
            rev_p = Path(tmp) / "review.md"
            rev_p.write_text(review_md, encoding="utf-8")
            record = parse_review_markdown(rev_p)
            self.assertEqual(record.verdict, "ACCEPTED")
            self.assertEqual(len(record.rows), 1)
            self.assertEqual(record.rows[0].criterion_id, "AC-01")
            self.assertEqual(record.rows[0].note, "Note with a | pipe character")

    def _setup_multi_criterion_change(self, change_id: str) -> tuple[Path, str, str]:
        """Helper to create and commit a change with multiple criteria (1 automated, 2 manual)."""
        change_dir = self.repo / ".goatee" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)

        contract_text = f"""# Change: {change_id}

## Intent

Implement multi-criterion feature {change_id}.

## Agreed Scope

### In Scope
- Multi-criterion support

### Out of Scope
- Extra features

## Acceptance Criteria

- AC-01: Automated unit test
  Evidence:
    type: automated
    command: python3 -c 'exit(0)'
    expected: exit 0

- AC-02: Manual security review
  Evidence:
    type: manual
    instruction: Review security controls

- AC-03: Manual UX audit
  Evidence:
    type: manual
    instruction: Audit user interface and CLI experience

## Build Budget

### Expected Surface
- src/{change_id}.py

### Reuse Strategy
- stdlib only

Dependency-Policy: No new runtime dependencies

### Blast Radius
- src/core/

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: {self.base_commit}
"""
        (change_dir / "change.md").write_text(contract_text, encoding="utf-8")

        src_dir = self.repo / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / f"{change_id}.py").write_text("# impl\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"feat: {change_id}"], cwd=self.repo, check=True)
        verified_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        receipt_text = f"""# Delivery Receipt: {change_id}

Verdict: NEEDS_REVIEW
Verified-At: 2026-09-21 00:00:00Z

## Commit Provenance

Baseline-Commit: {self.base_commit}
Verified-Commit: {verified_commit}

## Requirement Coverage Matrix

| Criterion | Type | Declared Verification | Observed Result | Status |
| :--- | :---: | :--- | :--- | :---: |
| **AC-01** | automated | `python3 -c 'exit(0)'` | exit 0 (0.01s) | **VERIFIED** |
| **AC-02** | manual | `Review security controls` | Pending human observation and sign-off | **NEEDS_REVIEW** |
| **AC-03** | manual | `Audit user interface and CLI experience` | Pending human observation and sign-off | **NEEDS_REVIEW** |

## Build Budget & Surface Analysis

### Expected Changes
- src/{change_id}.py

### Surface Deviations
- (none)

### Protected Changes
- (none)

## Git-Visible Mutation Report

- Zero Git-visible repository mutations detected during verification.

## Diagnostic Excerpt

- No diagnostics recorded (all executed checks clean).
"""
        receipt_path = change_dir / "receipt.md"
        receipt_path.write_text(receipt_text, encoding="utf-8")

        subprocess.run(["git", "add", str(receipt_path)], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", f"chore: record receipt for {change_id}"], cwd=self.repo, check=True)
        receipt_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        return change_dir, verified_commit, receipt_commit

    def test_interactive_attestation_multi_criterion(self):
        """AC-03: Interactive wizard collects multiple criterion decisions in memory and writes review.md atomically."""
        change_id = "015-interactive"
        change_dir, _, _ = self._setup_multi_criterion_change(change_id)

        # 1. Test multi-decision session
        # Inputs: AC-02 -> v, note; AC-03 -> r, note
        responses = iter([
            "v",
            "Security checks verified and approved",
            "r",
            "UX flow rejected due to confusing prompts",
        ])
        ret = run_interactive_attestation(
            repo_root=self.repo,
            change_id=change_id,
            input_fn=lambda _: next(responses),
            output_fn=lambda _: None,
        )
        self.assertEqual(ret, 0)

        review_path = change_dir / "review.md"
        self.assertTrue(review_path.is_file())
        review = parse_review_markdown(review_path)
        self.assertEqual(review.verdict, "REJECTED")
        self.assertEqual(len(review.events), 2)
        self.assertEqual(review.events[0].criterion_id, "AC-02")
        self.assertEqual(review.events[0].observation, "VERIFIED")
        self.assertEqual(review.events[0].note, "Security checks verified and approved")
        self.assertEqual(review.events[1].criterion_id, "AC-03")
        self.assertEqual(review.events[1].observation, "REJECTED")
        self.assertEqual(review.events[1].note, "UX flow rejected due to confusing prompts")

        # 2. Test quitting without decisions results in no review file
        change_id_quit = "015-quit"
        change_dir_quit, _, _ = self._setup_multi_criterion_change(change_id_quit)
        ret_quit = run_interactive_attestation(
            repo_root=self.repo,
            change_id=change_id_quit,
            input_fn=lambda _: "q",
            output_fn=lambda _: None,
        )
        self.assertEqual(ret_quit, 0)
        self.assertFalse((change_dir_quit / "review.md").exists())

    def test_batch_attestation_explicit_decisions(self):
        """AC-04: Non-interactive batch attestation processes explicit --decision entries with upfront validation."""
        # 1. Test parse_decision_argument format parsing
        cid, v, n = parse_decision_argument("AC-02=VERIFIED:All security controls verified")
        self.assertEqual(cid, "AC-02")
        self.assertEqual(v, "VERIFIED")
        self.assertEqual(n, "All security controls verified")

        cid, v, n = parse_decision_argument('AC-03=REJECTED:"Flaky UI behavior"')
        self.assertEqual(cid, "AC-03")
        self.assertEqual(v, "REJECTED")
        self.assertEqual(n, "Flaky UI behavior")

        with self.assertRaises(ControlStateError):
            parse_decision_argument("AC-02")
        with self.assertRaises(ControlStateError):
            parse_decision_argument("AC-02=INVALID:note")
        with self.assertRaises(ControlStateError):
            parse_decision_argument("AC-02=VERIFIED")

        # 2. Test record_multi_attestation with multiple explicit decisions
        change_id = "015-batch"
        change_dir, _, _ = self._setup_multi_criterion_change(change_id)

        record_multi_attestation(
            repo_root=self.repo,
            change_id=change_id,
            decisions=[
                ("AC-02", "VERIFIED", "Security confirmed"),
                ("AC-03", "VERIFIED", "UX confirmed"),
            ],
        )

        review_path = change_dir / "review.md"
        self.assertTrue(review_path.is_file())
        review = parse_review_markdown(review_path)
        self.assertEqual(review.verdict, "ACCEPTED")
        self.assertEqual(len(review.events), 2)
        self.assertEqual(review.events[0].criterion_id, "AC-02")
        self.assertEqual(review.events[0].observation, "VERIFIED")
        self.assertEqual(review.events[1].criterion_id, "AC-03")
        self.assertEqual(review.events[1].observation, "VERIFIED")

        # 3. Test CLI integration with repeatable --decision flags
        change_id_cli = "015-cli-batch"
        change_dir_cli, _, _ = self._setup_multi_criterion_change(change_id_cli)

        repo_root_code = Path(__file__).resolve().parent.parent
        env = dict(os.environ, PYTHONPATH=str(repo_root_code))
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "goatee.cli",
                "attest",
                change_id_cli,
                "--decision",
                "AC-02=VERIFIED:Security passed",
                "--decision",
                "AC-03=REJECTED:UX failed",
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0, f"CLI stderr: {res.stderr}")
        cli_review = parse_review_markdown(change_dir_cli / "review.md")
        self.assertEqual(cli_review.verdict, "REJECTED")
        self.assertEqual(len(cli_review.events), 2)

    def test_attestation_security_regressions(self):
        """AC-05: Attestation security controls reject automated criterion verification, uncommitted receipts, and malformed decisions."""
        # SEC-01: Prohibit positive attestation of automated criteria
        change_id_sec1 = "015-sec01"
        change_dir_sec1, _, _ = self._setup_multi_criterion_change(change_id_sec1)

        with self.assertRaises(ControlStateError) as ctx:
            record_multi_attestation(
                repo_root=self.repo,
                change_id=change_id_sec1,
                decisions=[("AC-01", "VERIFIED", "Attempting positive verification on automated criterion")],
            )
        self.assertIn("Human attestation with VERIFIED is only permitted for 'type: manual' criteria", str(ctx.exception))
        self.assertFalse((change_dir_sec1 / "review.md").exists())

        # SEC-02: Reject duplicate or conflicting decisions within the same batch before mutation
        change_id_sec2 = "015-sec02"
        change_dir_sec2, _, _ = self._setup_multi_criterion_change(change_id_sec2)

        with self.assertRaises(ControlStateError) as ctx:
            record_multi_attestation(
                repo_root=self.repo,
                change_id=change_id_sec2,
                decisions=[
                    ("AC-02", "VERIFIED", "First decision"),
                    ("AC-02", "REJECTED", "Conflicting second decision"),
                ],
            )
        self.assertIn("Duplicate decision for criterion 'AC-02'", str(ctx.exception))
        self.assertFalse((change_dir_sec2 / "review.md").exists())

        # SEC-03: Reject attestation if application files or receipt are uncommitted
        change_id_sec3 = "015-sec03"
        change_dir_sec3, _, _ = self._setup_multi_criterion_change(change_id_sec3)

        # Modify application file
        (self.repo / "src" / f"{change_id_sec3}.py").write_text("# dirty edit\n", encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            record_multi_attestation(
                repo_root=self.repo,
                change_id=change_id_sec3,
                decisions=[("AC-02", "VERIFIED", "Valid decision")],
            )
        self.assertIn("Working tree must be clean", str(ctx.exception))
        self.assertFalse((change_dir_sec3 / "review.md").exists())

        # Reset application file
        subprocess.run(["git", "checkout", "--", "."], cwd=self.repo, check=True)

        # Modify receipt
        (change_dir_sec3 / "receipt.md").write_text("# dirty receipt\n", encoding="utf-8")
        with self.assertRaises(ControlStateError) as ctx:
            record_multi_attestation(
                repo_root=self.repo,
                change_id=change_id_sec3,
                decisions=[("AC-02", "VERIFIED", "Valid decision")],
            )
        self.assertIn("Working tree must be clean", str(ctx.exception))
        self.assertFalse((change_dir_sec3 / "review.md").exists())


if __name__ == "__main__":
    unittest.main()
