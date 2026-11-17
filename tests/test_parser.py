"""Unit tests for the narrow metadata and Markdown parser."""

import re
import tempfile
import unittest
from pathlib import Path
from goatee.core.parser import (
    parse_change_contract,
    parse_metadata_block,
    update_contract_state,
    compute_contract_hash,
    ContractSyntaxError,
)


SAMPLE_CHANGE_MD = """# Change: 001-export-csv

## Intent

Allow users to export filtered records to CSV safely.

## Agreed Scope

### In Scope
- Export button in header
- CSV generation

### Out of Scope
- PDF export
- Background queue

## Acceptance Criteria

- AC-01: User can export records to CSV format
  Evidence:
    type: automated
    command: pytest tests/test_export.py -k test_csv
    expected: exit 0

- AC-02: Export button appears beside Filters panel
  Evidence:
    type: manual
    instruction: Confirm Export button is visible beside Filters

## Build Budget

### Expected Surface
- src/export/
- tests/test_export.py

### Reuse Strategy
- src/utils/csv_writer.py

Dependency-Policy: No new runtime dependencies

### Blast Radius
- src/auth/
- src/billing/

## Contract State

Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: abc123def456
"""


class TestParser(unittest.TestCase):

    def test_parse_metadata_basic(self):
        text = "Key: Value\nOther-Key: Some Value Here\n"
        meta = parse_metadata_block(text)
        self.assertEqual(meta["Key"], "Value")
        self.assertEqual(meta["Other-Key"], "Some Value Here")

    def test_parse_metadata_ignores_comments_and_blanks(self):
        text = "\n# Comment\nKey: Value\n\n# Another comment\n"
        meta = parse_metadata_block(text)
        self.assertEqual(meta, {"Key": "Value"})

    def test_parse_metadata_colon_in_value(self):
        text = "Baseline-Commit: git:commit:12345\n"
        meta = parse_metadata_block(text)
        self.assertEqual(meta["Baseline-Commit"], "git:commit:12345")

    def test_parse_change_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cdir = Path(tmpdir) / "001-export-csv"
            cdir.mkdir()
            f = cdir / "change.md"
            f.write_text(SAMPLE_CHANGE_MD, encoding="utf-8")

            contract = parse_change_contract(f)
            self.assertEqual(contract.change_id, "001-export-csv")
            self.assertEqual(contract.contract_state.agreement_status, "APPROVED")
            self.assertEqual(contract.contract_state.budget_status, "APPROVED")
            self.assertEqual(contract.contract_state.baseline_commit, "abc123def456")

            # Agreed scope
            self.assertEqual(contract.agreed_scope_in, ["Export button in header", "CSV generation"])
            self.assertEqual(contract.agreed_scope_out, ["PDF export", "Background queue"])

            # Acceptance criteria
            self.assertEqual(len(contract.acceptance_criteria), 2)
            ac1 = contract.acceptance_criteria[0]
            self.assertEqual(ac1.id, "AC-01")
            self.assertEqual(ac1.evidence_type, "automated")
            self.assertEqual(ac1.command, "pytest tests/test_export.py -k test_csv")
            self.assertEqual(ac1.expected, "exit 0")

            ac2 = contract.acceptance_criteria[1]
            self.assertEqual(ac2.id, "AC-02")
            self.assertEqual(ac2.evidence_type, "manual")
            self.assertEqual(ac2.instruction, "Confirm Export button is visible beside Filters")

            # Budget
            self.assertIsNotNone(contract.build_budget)
            self.assertEqual(contract.build_budget.expected_surface, ["src/export/", "tests/test_export.py"])
            self.assertEqual(contract.build_budget.reuse_strategy, ["src/utils/csv_writer.py"])
            self.assertEqual(contract.build_budget.dependency_policy, "No new runtime dependencies")
            self.assertEqual(contract.build_budget.blast_radius, ["src/auth/", "src/billing/"])

    def test_update_contract_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cdir = Path(tmpdir) / "001-export-csv"
            cdir.mkdir()
            f = cdir / "change.md"
            f.write_text(SAMPLE_CHANGE_MD, encoding="utf-8")

            update_contract_state(f, "Baseline-Commit", "999888777")
            contract = parse_change_contract(f)
            self.assertEqual(contract.contract_state.baseline_commit, "999888777")

    def test_strict_contract_syntax_errors(self):
        """AC-07: Parser raises ContractSyntaxError with context when required structure is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cdir = Path(tmpdir) / "001-export-csv"
            cdir.mkdir()
            f = cdir / "change.md"

            # 1. Missing Contract State
            content_no_state = SAMPLE_CHANGE_MD.split("## Contract State")[0]
            f.write_text(content_no_state, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("## Contract State", str(ctx.exception))

            # 2. Missing Agreement-Status
            content_no_agree = SAMPLE_CHANGE_MD.replace("Agreement-Status: APPROVED\n", "")
            f.write_text(content_no_agree, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("Agreement-Status", str(ctx.exception))

            # 3. Missing Budget-Status
            content_no_budget = SAMPLE_CHANGE_MD.replace("Budget-Status: APPROVED\n", "")
            f.write_text(content_no_budget, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("Budget-Status", str(ctx.exception))

            # 4. Missing Build Budget
            content_no_bb = SAMPLE_CHANGE_MD.replace("## Build Budget", "## Something Else")
            f.write_text(content_no_bb, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("## Build Budget", str(ctx.exception))

            # 5. Missing Expected Surface
            content_no_es = SAMPLE_CHANGE_MD.replace("### Expected Surface", "### Some Surface")
            f.write_text(content_no_es, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("### Expected Surface", str(ctx.exception))

            # 6. Missing In Scope
            content_no_in_scope = SAMPLE_CHANGE_MD.replace("### In Scope", "### Some Scope")
            f.write_text(content_no_in_scope, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("### In Scope", str(ctx.exception))

            # 7. Missing Out of Scope
            content_no_out_scope = SAMPLE_CHANGE_MD.replace("### Out of Scope", "### Other Scope")
            f.write_text(content_no_out_scope, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("### Out of Scope", str(ctx.exception))

            # 8. Missing Reuse Strategy
            content_no_reuse = SAMPLE_CHANGE_MD.replace("### Reuse Strategy", "### Other Strategy")
            f.write_text(content_no_reuse, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("### Reuse Strategy", str(ctx.exception))

            # 9. Missing Blast Radius
            content_no_br = SAMPLE_CHANGE_MD.replace("### Blast Radius", "### Other Radius")
            f.write_text(content_no_br, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("### Blast Radius", str(ctx.exception))

            # 10. Missing Dependency-Policy:
            content_no_dep = SAMPLE_CHANGE_MD.replace("Dependency-Policy: No new runtime dependencies", "")
            f.write_text(content_no_dep, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("Dependency-Policy", str(ctx.exception))

            # 11. Missing Baseline-Commit key entirely
            content_no_baseline_key = SAMPLE_CHANGE_MD.replace("Baseline-Commit: abc123def456\n", "")
            f.write_text(content_no_baseline_key, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("Baseline-Commit", str(ctx.exception))

            # 12. Zero parsed Acceptance Criteria
            content_no_acs = re.sub(r"- AC-\d+:.*?(?=## Build Budget)", "", SAMPLE_CHANGE_MD, flags=re.DOTALL)
            f.write_text(content_no_acs, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("must declare at least one Acceptance Criterion", str(ctx.exception))

            # 13. Mismatched directory name vs change-id
            other_dir = Path(tmpdir) / "wrong-id"
            other_dir.mkdir()
            f_wrong = other_dir / "change.md"
            f_wrong.write_text(SAMPLE_CHANGE_MD, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f_wrong)
            self.assertIn("does not match directory name", str(ctx.exception))

            # 14. Invalid status enum (not DRAFT or APPROVED)
            content_bad_status = SAMPLE_CHANGE_MD.replace("Agreement-Status: APPROVED", "Agreement-Status: PENDING")
            f.write_text(content_bad_status, encoding="utf-8")
            with self.assertRaises(ContractSyntaxError) as ctx:
                parse_change_contract(f)
            self.assertIn("Invalid Agreement-Status 'PENDING'", str(ctx.exception))

    def test_valid_lifecycle_draft_states(self):
        """AC-07: Agreement-Status: DRAFT, Budget-Status: DRAFT, and blank Baseline-Commit are valid syntax."""
        draft_content = SAMPLE_CHANGE_MD.replace(
            "Agreement-Status: APPROVED\nBudget-Status: APPROVED\nBaseline-Commit: abc123def456",
            "Agreement-Status: DRAFT\nBudget-Status: DRAFT\nBaseline-Commit:"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cdir = Path(tmpdir) / "001-export-csv"
            cdir.mkdir()
            f = cdir / "change.md"
            f.write_text(draft_content, encoding="utf-8")

            contract = parse_change_contract(f)
            self.assertEqual(contract.contract_state.agreement_status, "DRAFT")
            self.assertEqual(contract.contract_state.budget_status, "DRAFT")
            self.assertIsNone(contract.contract_state.baseline_commit)

    def test_compute_contract_hash_lifecycle_invariance(self):
        """Contract hash must be invariant across lifecycle transitions (LANDED, Landed-At, Archived-At)."""
        base_hash = compute_contract_hash(SAMPLE_CHANGE_MD)

        landed_content = SAMPLE_CHANGE_MD + "\nLifecycle-Status: LANDED\nLanded-At: 2026-11-17 12:00:00Z\n"
        self.assertEqual(compute_contract_hash(landed_content), base_hash)

        archived_content = SAMPLE_CHANGE_MD + "\nLifecycle-Status: SUPERSEDED\nArchived-At: 2026-11-17 12:00:00Z\n"
        self.assertEqual(compute_contract_hash(archived_content), base_hash)


if __name__ == "__main__":
    unittest.main()
