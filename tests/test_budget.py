"""Unit tests for deterministic Build Budget comparison (AC-05 through AC-09)."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from goatee.core.models import BuildBudget
from goatee.core.budget import (
    compare_budget,
    get_git_changed_files,
    matches_path_entry,
    is_control_artifact,
)


class TestBudgetComparison(unittest.TestCase):

    def setUp(self):
        self.sample_budget = BuildBudget(
            expected_surface=[
                "goatee/cli.py",
                "goatee/core/budget.py",
                "goatee/templates/handoff.md",
                "tests/test_budget.py",
                "tests/test_cli.py",
                "README.md",
            ],
            reuse_strategy=[
                "Existing Phase 1 parser",
                "Git CLI",
            ],
            dependency_policy="No new runtime dependencies",
            blast_radius=[
                "goatee/core/parser.py",
                "goatee/templates/project.md",
                "goatee/templates/change.md",
                "Phase 1 approval semantics (goatee approve)",  # semantic safeguard entry
            ],
        )

    def test_files_inside_expected_surface(self):
        """AC-05: Budget comparison identifies files inside Expected Surface."""
        changed = [
            "goatee/cli.py",
            "goatee/core/budget.py",
            "tests/test_budget.py",
        ]
        result = compare_budget(changed, self.sample_budget)
        self.assertEqual(result.expected_changes, sorted(changed))
        self.assertEqual(result.surface_deviations, [])
        self.assertEqual(result.protected_changes, [])

    def test_files_outside_expected_surface(self):
        """AC-06: Budget comparison reports files outside Expected Surface."""
        changed = [
            "goatee/cli.py",
            "unapproved/surprise.py",
            "package.json",
        ]
        result = compare_budget(changed, self.sample_budget)
        self.assertEqual(result.expected_changes, ["goatee/cli.py"])
        self.assertEqual(result.surface_deviations, ["package.json", "unapproved/surprise.py"])
        self.assertEqual(result.protected_changes, [])

    def test_blast_radius_modifications(self):
        """AC-07: Budget comparison reports modifications to path-based Blast Radius paths."""
        changed = [
            "goatee/cli.py",
            "goatee/core/parser.py",  # In blast radius!
        ]
        result = compare_budget(changed, self.sample_budget)
        self.assertIn("goatee/core/parser.py", result.protected_changes)
        # Also note goatee/core/parser.py is not in expected surface, so it's a deviation too
        self.assertIn("goatee/core/parser.py", result.surface_deviations)

    def test_control_artifacts_ignored(self):
        """AC-08: .goatee/** changes do not count as application-surface deviations."""
        changed = [
            "goatee/cli.py",
            ".goatee/project.md",
            ".goatee/changes/002-handoff-budget/change.md",
            ".goatee/changes/001-init/receipt.md",
        ]
        result = compare_budget(changed, self.sample_budget)
        self.assertEqual(result.expected_changes, ["goatee/cli.py"])
        self.assertEqual(result.surface_deviations, [])
        self.assertEqual(
            result.ignored_control_files,
            [
                ".goatee/changes/001-init/receipt.md",
                ".goatee/changes/002-handoff-budget/change.md",
                ".goatee/project.md",
            ]
        )

    def test_directory_prefix_matching(self):
        """AC-09: Directory entries in Expected Surface match descendants recursively."""
        budget = BuildBudget(
            expected_surface=["src/", "tests"],
            blast_radius=["config/"],
        )
        changed = [
            "src/components/button.py",
            "src/deep/nested/item.py",
            "tests/test_something.py",
            "tests/sub/test_other.py",
            "config/settings.py",
            "other/file.py",
        ]
        result = compare_budget(changed, budget)
        self.assertEqual(
            result.expected_changes,
            [
                "src/components/button.py",
                "src/deep/nested/item.py",
                "tests/sub/test_other.py",
                "tests/test_something.py",
            ]
        )
        self.assertEqual(result.surface_deviations, ["config/settings.py", "other/file.py"])
        self.assertEqual(result.protected_changes, ["config/settings.py"])

    def test_git_changed_files_integration(self):
        """Integration test with a real Git repo testing get_git_changed_files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            subprocess.run(["git", "init"], check=True, capture_output=True, cwd=p)
            subprocess.run(["git", "config", "user.email", "test@test.local"], check=True, cwd=p)
            subprocess.run(["git", "config", "user.name", "Tester"], check=True, cwd=p)

            # Baseline commit
            (p / "base.py").write_text("a = 1", encoding="utf-8")
            subprocess.run(["git", "add", "base.py"], check=True, cwd=p)
            subprocess.run(["git", "commit", "-m", "Baseline"], check=True, capture_output=True, cwd=p)
            base_sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, cwd=p).stdout.strip()

            # New changes
            (p / "new_file.py").write_text("b = 2", encoding="utf-8")
            (p / "base.py").write_text("a = 2", encoding="utf-8")
            subprocess.run(["git", "add", "."], check=True, cwd=p)
            subprocess.run(["git", "commit", "-m", "Work"], check=True, capture_output=True, cwd=p)

            changed = get_git_changed_files(base_sha, "HEAD", cwd=p)
            self.assertEqual(sorted(changed), ["base.py", "new_file.py"])


if __name__ == "__main__":
    unittest.main()
