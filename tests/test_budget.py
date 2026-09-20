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
    normalize_relpath,
    GitChange,
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
            self.assertEqual(sorted(c.path for c in changed), ["base.py", "new_file.py"])
            statuses = {c.path: c.status for c in changed}
            self.assertEqual(statuses["base.py"], "M")
            self.assertEqual(statuses["new_file.py"], "A")

    def test_preserves_diff_operation_semantics(self):
        """AC-08: Git delta discovery preserves A/M/D/R and rename handling across control boundaries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            subprocess.run(["git", "init"], check=True, capture_output=True, cwd=p)
            subprocess.run(["git", "config", "user.email", "test@test.local"], check=True, cwd=p)
            subprocess.run(["git", "config", "user.name", "Tester"], check=True, cwd=p)

            # Baseline commit
            (p / "app.py").write_text("orig = 1", encoding="utf-8")
            (p / "old_name.py").write_text("rename_me = 1", encoding="utf-8")
            (p / "to_delete.py").write_text("delete_me = 1", encoding="utf-8")
            (p / ".goatee").mkdir()
            (p / ".goatee" / "project.md").write_text("# Project", encoding="utf-8")
            subprocess.run(["git", "add", "."], check=True, cwd=p)
            subprocess.run(["git", "commit", "-m", "Baseline"], check=True, capture_output=True, cwd=p)
            base_sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, cwd=p).stdout.strip()

            # Perform A, M, D, and R operations
            (p / "added.py").write_text("added = 1", encoding="utf-8")
            (p / "app.py").write_text("orig = 2", encoding="utf-8")
            (p / "to_delete.py").unlink()
            subprocess.run(["git", "mv", "old_name.py", "new_name.py"], check=True, cwd=p)
            subprocess.run(["git", "add", "-A"], check=True, cwd=p)
            subprocess.run(["git", "commit", "-m", "Delta operations"], check=True, capture_output=True, cwd=p)

            # Test discovery
            changes = get_git_changed_files(base_sha, "HEAD", cwd=p)
            change_map = {c.path: c for c in changes}

            self.assertIn("added.py", change_map)
            self.assertEqual(change_map["added.py"].status, "A")

            self.assertIn("app.py", change_map)
            self.assertEqual(change_map["app.py"].status, "M")

            self.assertIn("to_delete.py", change_map)
            self.assertEqual(change_map["to_delete.py"].status, "D")

            self.assertIn("new_name.py", change_map)
            self.assertEqual(change_map["new_name.py"].status, "R")
            self.assertEqual(change_map["new_name.py"].old_path, "old_name.py")

            # Test compare_budget with GitChange objects
            budget = BuildBudget(
                expected_surface=["added.py", "app.py", "new_name.py"],
                blast_radius=["protected_file.py"],
                reuse_strategy=[],
                dependency_policy=""
            )
            comp = compare_budget(changes, budget)
            self.assertIn("added.py", comp.expected_changes)
            self.assertIn("app.py", comp.expected_changes)
            self.assertIn("new_name.py", comp.expected_changes)
            # to_delete.py was not budgeted
            self.assertIn("to_delete.py", comp.surface_deviations)

        # Test cross-boundary rename policies
        budget_boundary = BuildBudget(
            expected_surface=["src/app.py", "src/renamed_app.py"],
            blast_radius=["src/old_app.py", "src/protected.py"],
            reuse_strategy=[],
            dependency_policy=""
        )
        # 1. control -> control
        ctrl_ctrl = [GitChange(status="R", path=".goatee/new.md", old_path=".goatee/old.md")]
        res1 = compare_budget(ctrl_ctrl, budget_boundary)
        self.assertEqual(res1.ignored_control_files, [".goatee/new.md"])
        self.assertEqual(res1.expected_changes, [])
        self.assertEqual(res1.surface_deviations, [])

        # 2. control -> application
        # 2a. Expected new application path
        ctrl_app_expected = [GitChange(status="R", path="src/app.py", old_path=".goatee/templates/app.py")]
        res2a = compare_budget(ctrl_app_expected, budget_boundary)
        self.assertIn("src/app.py", res2a.expected_changes)
        self.assertEqual(res2a.surface_deviations, [])

        # 2b. Unexpected new application path
        ctrl_app_unexpected = [GitChange(status="R", path="src/unbudgeted.py", old_path=".goatee/templates/unbudgeted.py")]
        res2b = compare_budget(ctrl_app_unexpected, budget_boundary)
        self.assertIn("src/unbudgeted.py", res2b.surface_deviations)

        # 3. application -> application
        # Evaluates NEW path against Expected Surface; evaluates BOTH old and new against Blast Radius.
        # Note: Old path does NOT need to be in Expected Surface.
        app_app = [GitChange(status="R", path="src/renamed_app.py", old_path="src/old_app.py")]
        res3 = compare_budget(app_app, budget_boundary)
        self.assertIn("src/renamed_app.py", res3.expected_changes)
        self.assertIn("src/old_app.py", res3.protected_changes)
        self.assertEqual(res3.surface_deviations, [])

        # 4. application -> control
        # Old application path evaluated against Expected Surface / deviations
        app_ctrl_unexpected = [GitChange(status="R", path=".goatee/archive/app.py", old_path="src/unexpected_old.py")]
        res4a = compare_budget(app_ctrl_unexpected, budget_boundary)
        self.assertIn("src/unexpected_old.py", res4a.surface_deviations)

        app_ctrl_expected = [GitChange(status="R", path=".goatee/archive/app.py", old_path="src/app.py")]
        res4b = compare_budget(app_ctrl_expected, budget_boundary)
        self.assertIn("src/app.py", res4b.expected_changes)
        self.assertEqual(res4b.surface_deviations, [])

        # 5. Git D + A preservation (no synthesis of R)
        d_and_a = [
            GitChange(status="D", path="src/old_file.py"),
            GitChange(status="A", path="src/new_file.py"),
        ]
        res5 = compare_budget(d_and_a, budget_boundary)
        self.assertEqual([c.status for c in res5.raw_changes], ["D", "A"])

    def test_path_normalization_mixed_separators(self):
        """AC-11: Path normalization unit tests prove mixed path separators normalize predictably."""
        self.assertEqual(normalize_relpath("goatee\\cli.py"), "goatee/cli.py")
        self.assertEqual(normalize_relpath("/goatee/core/verifier.py"), "goatee/core/verifier.py")
        self.assertEqual(normalize_relpath("\\tests\\test_cli.py"), "tests/test_cli.py")
        self.assertEqual(normalize_relpath("///a/b/c.py"), "a/b/c.py")
        self.assertEqual(normalize_relpath("a\\b\\c/d.py"), "a/b/c/d.py")

        # Matching with mixed separators
        self.assertTrue(matches_path_entry("goatee\\cli.py", "goatee/cli.py"))
        self.assertTrue(matches_path_entry("tests\\unit\\test_a.py", "tests/"))
        self.assertTrue(matches_path_entry("tests/unit/test_a.py", "tests\\"))


if __name__ == "__main__":
    unittest.main()
