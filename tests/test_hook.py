"""Unit and integration tests for GoATee Git pre-commit guardrail hook."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from goatee.cli import build_parser, cmd_hook


class TestGitHook(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Hook Tester"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "hook@example.com"], cwd=self.repo_root, check=True)

        (self.repo_root / "README.md").write_text("# Test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_root, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_hook_install_and_uninstall(self):
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.repo_root)
            parser = build_parser()

            # 1. Install hook
            args_install = parser.parse_args(["hook", "install"])
            ret_in = cmd_hook(args_install)
            self.assertEqual(ret_in, 0)

            hook_file = self.repo_root / ".git" / "hooks" / "pre-commit"
            self.assertTrue(hook_file.is_file())
            content = hook_file.read_text(encoding="utf-8")
            self.assertIn("GoATee Pre-Commit Guardrail", content)
            self.assertIn("python3 -m goatee.cli check", content)

            # Check executable permission
            self.assertTrue(os.access(hook_file, os.X_OK))

            # Idempotent install
            ret_in2 = cmd_hook(args_install)
            self.assertEqual(ret_in2, 0)

            # 2. Uninstall hook
            args_uninstall = parser.parse_args(["hook", "uninstall"])
            ret_un = cmd_hook(args_uninstall)
            self.assertEqual(ret_un, 0)

            self.assertFalse(hook_file.is_file())
        finally:
            os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
