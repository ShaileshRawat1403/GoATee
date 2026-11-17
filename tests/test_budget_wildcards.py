"""Unit tests for path budget wildcard and prefix matching in GoATee."""

import unittest
from goatee.core.budget import matches_path_entry, compare_budget, GitChange
from goatee.core.models import BuildBudget


class TestBudgetWildcards(unittest.TestCase):

    def test_exact_match(self):
        self.assertTrue(matches_path_entry("goatee/cli.py", "goatee/cli.py"))
        self.assertFalse(matches_path_entry("goatee/cli.py", "goatee/core.py"))

    def test_directory_prefix_match(self):
        self.assertTrue(matches_path_entry("tests/test_x.py", "tests/"))
        self.assertTrue(matches_path_entry("tests/test_x.py", "tests"))
        self.assertTrue(matches_path_entry("tests/sub/test_y.py", "tests"))
        self.assertFalse(matches_path_entry("tests_other/test_x.py", "tests"))

    def test_directory_double_star_wildcard(self):
        self.assertTrue(matches_path_entry("src/nested/app.py", "src/**"))
        self.assertTrue(matches_path_entry("src/app.py", "src/**"))
        self.assertTrue(matches_path_entry(".goatee/changes/002/change.md", ".goatee/changes/002/**"))
        self.assertFalse(matches_path_entry("other/app.py", "src/**"))

    def test_directory_single_star_wildcard(self):
        self.assertTrue(matches_path_entry("src/nested/app.py", "src/*"))
        self.assertTrue(matches_path_entry("src/app.py", "src/*"))
        self.assertFalse(matches_path_entry("other/app.py", "src/*"))

    def test_fnmatch_glob(self):
        self.assertTrue(matches_path_entry("tests/test_app.py", "tests/test_*.py"))
        self.assertFalse(matches_path_entry("tests/helper.py", "tests/test_*.py"))

    def test_compare_budget_with_wildcards(self):
        budget = BuildBudget(
            expected_surface=["src/**", "tests/*"],
            reuse_strategy=[],
            dependency_policy="No new dependencies",
            blast_radius=["config/**", "auth.py"]
        )
        changes = [
            GitChange(status="M", path="src/core/engine.py"),
            GitChange(status="A", path="tests/test_engine.py"),
            GitChange(status="M", path="docs/index.md"),
            GitChange(status="M", path="config/db.py"),
        ]
        res = compare_budget(changes, budget)
        self.assertEqual(res.expected_changes, ["src/core/engine.py", "tests/test_engine.py"])
        self.assertEqual(res.surface_deviations, ["config/db.py", "docs/index.md"])
        self.assertEqual(res.protected_changes, ["config/db.py"])


if __name__ == "__main__":
    unittest.main()
