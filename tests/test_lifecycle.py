"""Unit tests for GoATee change lifecycle management (land, archive)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from goatee.cli import build_parser
from goatee.core.lifecycle import land_change, archive_change, LifecycleError
from goatee.core.status import get_project_status, format_project_overview


class TestLifecycle(unittest.TestCase):

    def _setup_repo(self, root: Path, change_id: str = "001-feat"):
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=root, check=True)
        (root / "README.md").write_text("# Test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)

        parser = build_parser()
        orig_cwd = os.getcwd()
        os.chdir(root)
        try:
            parser.parse_args(["init"]).func(parser.parse_args(["init"]))
            parser.parse_args(["new", change_id]).func(parser.parse_args(["new", change_id]))
        finally:
            os.chdir(orig_cwd)

        c_dir = root / ".goatee" / "changes" / change_id
        # Update with simple test command
        c_path = c_dir / "change.md"
        content = c_path.read_text(encoding="utf-8")
        content = content.replace("<test command>", "true")
        content = content.replace("<description>", "pass")
        content = content.replace("<what to observe>", "visual check")
        content = content.replace("- <file/directory path 1>", "- app.py")
        content = content.replace("- <file/directory path 2>", "- (none)")
        content = content.replace("Agreement-Status: DRAFT", "Agreement-Status: APPROVED")
        content = content.replace("Budget-Status: DRAFT", "Budget-Status: APPROVED")
        c_path.write_text(content, encoding="utf-8")
        return c_dir

    def test_land_requires_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._setup_repo(root, "001-feat")
            with self.assertRaises(LifecycleError) as cm:
                land_change(root, "001-feat")
            self.assertIn("delivery receipt missing", str(cm.exception))

    def test_land_requires_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c_dir = self._setup_repo(root, "001-feat")
            (c_dir / "receipt.md").write_text("# Delivery Receipt: 001-feat\nVerdict: PASS\n", encoding="utf-8")
            with self.assertRaises(LifecycleError) as cm:
                land_change(root, "001-feat")
            self.assertIn("human review missing", str(cm.exception))

    def test_land_requires_accepted_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c_dir = self._setup_repo(root, "001-feat")
            (c_dir / "receipt.md").write_text("# Delivery Receipt: 001-feat\nVerdict: NEEDS_REVIEW\n", encoding="utf-8")
            (c_dir / "review.md").write_text("# Human Review: 001-feat\nReview-Verdict: REJECTED\n", encoding="utf-8")
            with self.assertRaises(LifecycleError) as cm:
                land_change(root, "001-feat")
            self.assertIn("Must be 'ACCEPTED'", str(cm.exception))

    def test_land_full_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c_dir = self._setup_repo(root, "001-feat")
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(root)
            try:
                # 1. Approve
                rc = parser.parse_args(["approve", "001-feat"]).func(parser.parse_args(["approve", "001-feat"]))
                self.assertEqual(rc, 0)

                # 2. Add implemented file
                (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "implement 001-feat"], check=True, capture_output=True)

                # 3. Verify (exit 1 expected due to pending manual criteria)
                rc = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc, 1)
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "receipt"], check=True, capture_output=True)

                # 4. Attest
                rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "Approved"]).func(
                    parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "Approved"])
                )
                self.assertEqual(rc, 0)
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "review"], check=True, capture_output=True)

                # 5. Land
                dest = land_change(root, "001-feat")
                self.assertTrue(dest.is_dir())
                self.assertTrue((dest / "change.md").is_file())
                self.assertFalse((root / ".goatee" / "changes" / "001-feat").exists())
                self.assertFalse((root / ".goatee" / ".active").exists())

                subprocess.run(["git", "add", "-A"], check=True)
                subprocess.run(["git", "commit", "-m", "land 001-feat"], check=True, capture_output=True)

                # 6. Check status output
                rep = get_project_status(root)
                self.assertEqual(len(rep.changes), 0)
                self.assertEqual(len(rep.archived_changes), 1)

                out_default = format_project_overview(rep, show_archived=False)
                self.assertIn("Archived Changes: 1 (use --all to inspect)", out_default)

                out_all = format_project_overview(rep, show_archived=True)
                self.assertIn("Archived Changes:", out_all)
                self.assertIn("001-feat", out_all)
            finally:
                os.chdir(orig_cwd)

    def test_archive_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._setup_repo(root, "002-abandoned")
            dest = archive_change(root, "002-abandoned", reason="SUPERSEDED")
            self.assertTrue(dest.is_dir())
            content = (dest / "change.md").read_text(encoding="utf-8")
            self.assertIn("Lifecycle-Status: SUPERSEDED", content)
            self.assertFalse((root / ".goatee" / ".active").exists())


if __name__ == "__main__":
    unittest.main()
