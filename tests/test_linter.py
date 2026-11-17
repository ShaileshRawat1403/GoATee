"""Unit tests for GoATee semantic contract linter."""

import tempfile
import unittest
from pathlib import Path
from goatee.core.linter import lint_change_contract, format_lint_report


class TestLinter(unittest.TestCase):

    def test_lint_clean_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "001-clean"
            d.mkdir()
            p = d / "change.md"
            p.write_text("""# Change: 001-clean

## Intent
Clean test intent.

## Agreed Scope
### In Scope
- Scope 1
### Out of Scope
- None

## Acceptance Criteria
- AC-01: Run tests
  Evidence:
    type: automated
    command: pytest
    expected: exit 0

## Build Budget
### Expected Surface
- src/app.py
### Reuse Strategy
- None
Dependency-Policy: None
### Blast Radius
- config/db.py

## Contract State
Agreement-Status: DRAFT
Budget-Status: DRAFT
Baseline-Commit: 
""", encoding="utf-8")
            issues = lint_change_contract(p)
            errors = [i for i in issues if i.severity == "ERROR"]
            self.assertEqual(len(errors), 0)
            rep = format_lint_report("001-clean", issues)
            self.assertIn("Status: PASSED", rep)

    def test_lint_detects_conflict_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "001-conflict"
            d.mkdir()
            p = d / "change.md"
            p.write_text("""# Change: 001-conflict

## Intent
Intent.

## Agreed Scope
### In Scope
- Scope 1
### Out of Scope
- None

## Acceptance Criteria
- AC-01: Run tests
  Evidence:
    type: automated
    command: pytest
    expected: exit 0

## Build Budget
### Expected Surface
- src/auth/login.py
- src/app.py
### Reuse Strategy
- None
Dependency-Policy: None
### Blast Radius
- src/auth/**

## Contract State
Agreement-Status: DRAFT
Budget-Status: DRAFT
Baseline-Commit: 
""", encoding="utf-8")
            issues = lint_change_contract(p)
            errors = [i for i in issues if i.severity == "ERROR"]
            self.assertTrue(any("FATAL CONFLICT" in e.message for e in errors))
            rep = format_lint_report("001-conflict", issues)
            self.assertIn("Status: FAILED", rep)

    def test_lint_detects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "001-traversal"
            d.mkdir()
            p = d / "change.md"
            p.write_text("""# Change: 001-traversal

## Intent
Intent.

## Agreed Scope
### In Scope
- Scope 1
### Out of Scope
- None

## Acceptance Criteria
- AC-01: Run tests
  Evidence:
    type: automated
    command: pytest
    expected: exit 0

## Build Budget
### Expected Surface
- ../../etc/passwd
### Reuse Strategy
- None
Dependency-Policy: None
### Blast Radius
- /var/log/syslog

## Contract State
Agreement-Status: DRAFT
Budget-Status: DRAFT
Baseline-Commit: 
""", encoding="utf-8")
            issues = lint_change_contract(p)
            errors = [i for i in issues if i.severity == "ERROR"]
            self.assertTrue(any("Directory traversal" in e.message for e in errors))
            self.assertTrue(any("Absolute path" in e.message for e in errors))

    def test_lint_detects_malformed_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "001-criteria"
            d.mkdir()
            p = d / "change.md"
            p.write_text("""# Change: 001-criteria

## Intent
Intent.

## Agreed Scope
### In Scope
- Scope 1
### Out of Scope
- None

## Acceptance Criteria
- AC-01: Run tests
  Evidence:
    type: automated
    command: 
    expected: exit 0
- AC-02: Manual check
  Evidence:
    type: manual
    instruction: 

## Build Budget
### Expected Surface
- src/app.py
### Reuse Strategy
- None
Dependency-Policy: None
### Blast Radius
- config/db.py

## Contract State
Agreement-Status: DRAFT
Budget-Status: DRAFT
Baseline-Commit: 
""", encoding="utf-8")
            issues = lint_change_contract(p)
            errors = [i for i in issues if i.severity == "ERROR"]
            self.assertTrue(any("missing required execution command" in e.message for e in errors))
            self.assertTrue(any("missing required human inspection instruction" in e.message for e in errors))

    def test_lint_nonexistent_file(self):
        p = Path("/tmp/does_not_exist_goatee_test/change.md")
        issues = lint_change_contract(p)
        errors = [i for i in issues if i.severity == "ERROR"]
        self.assertEqual(len(errors), 1)
        self.assertIn("not found", errors[0].message)


if __name__ == "__main__":
    unittest.main()
