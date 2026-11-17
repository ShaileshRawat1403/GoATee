"""Unit tests for GoATee native AI agent rules exporter."""

import tempfile
import unittest
from pathlib import Path
from goatee.core.rules import export_agent_rules, generate_rules_block, MARKER_START, MARKER_END


class TestRulesExport(unittest.TestCase):

    def test_export_claude_rules_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = export_agent_rules(root, target="claude")
            self.assertEqual(len(results), 1)
            claude_md = root / "CLAUDE.md"
            self.assertTrue(claude_md.is_file())
            content = claude_md.read_text(encoding="utf-8")
            self.assertIn(MARKER_START, content)
            self.assertIn("GoATee AI Delivery & Budget Governance", content)
            self.assertIn(MARKER_END, content)

    def test_export_preserves_surrounding_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cursor_rules = root / ".cursorrules"
            cursor_rules.write_text("# Custom Developer Notes\n\nAlways use TypeScript.\n", encoding="utf-8")

            results = export_agent_rules(root, target="cursor")
            self.assertEqual(len(results), 1)
            content = cursor_rules.read_text(encoding="utf-8")
            self.assertIn("# Custom Developer Notes", content)
            self.assertIn("Always use TypeScript.", content)
            self.assertIn(MARKER_START, content)

            # Re-export and verify idempotency (not duplicated)
            export_agent_rules(root, target="cursor")
            content2 = cursor_rules.read_text(encoding="utf-8")
            self.assertEqual(content2.count(MARKER_START), 1)
            self.assertEqual(content2.count(MARKER_END), 1)
            self.assertIn("Always use TypeScript.", content2)

    def test_export_all_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = export_agent_rules(root, target="all")
            self.assertEqual(len(results), 4)
            self.assertTrue((root / "CLAUDE.md").is_file())
            self.assertTrue((root / ".cursorrules").is_file())
            self.assertTrue((root / ".windsurfrules").is_file())
            self.assertTrue((root / ".gemini" / "rules").is_file())

    def test_export_with_active_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c_dir = root / ".goatee" / "changes" / "001-test"
            c_dir.mkdir(parents=True)
            (c_dir / "change.md").write_text("""# Change: 001-test

## Intent
Build the authentication widget.

## Agreed Scope
### In Scope
- Scope 1
### Out of Scope
- None

## Acceptance Criteria
- AC-01: Pass
  Evidence:
    type: automated
    command: pytest
    expected: exit 0

## Build Budget
### Expected Surface
- src/auth.py
- src/widget.tsx
### Reuse Strategy
- None
Dependency-Policy: No new runtime dependencies
### Blast Radius
- config/secrets.py

## Contract State
Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: 1234567890abcdef1234567890abcdef12345678
""", encoding="utf-8")
            (root / ".goatee" / ".active").write_text("001-test\n", encoding="utf-8")

            results = export_agent_rules(root, target="claude")
            content = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("Active Change: 001-test", content)
            self.assertIn("Build the authentication widget.", content)
            self.assertIn("- src/auth.py", content)
            self.assertIn("- config/secrets.py", content)


if __name__ == "__main__":
    unittest.main()
