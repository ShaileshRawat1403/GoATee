"""Unit tests for the narrow metadata and Markdown parser."""

import tempfile
import unittest
from pathlib import Path
from goatee.core.parser import parse_change_contract, parse_metadata_block, update_contract_state


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
            f = Path(tmpdir) / "change.md"
            f.write_text(SAMPLE_CHANGE_MD, encoding="utf-8")

            contract = parse_change_contract(f)
            self.assertEqual(contract.change_id, "001-export-csv")
            self.assertEqual(contract.contract_state.agreement_status, "APPROVED")
            self.assertEqual(contract.contract_state.budget_status, "APPROVED")
            self.assertEqual(contract.contract_state.baseline_commit, "abc123def456")

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
            f = Path(tmpdir) / "change.md"
            f.write_text(SAMPLE_CHANGE_MD, encoding="utf-8")

            update_contract_state(f, "Baseline-Commit", "999888777")
            contract = parse_change_contract(f)
            self.assertEqual(contract.contract_state.baseline_commit, "999888777")


if __name__ == "__main__":
    unittest.main()
