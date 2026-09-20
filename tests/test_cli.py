"""Unit tests for the GoATee CLI commands: init, new, approve."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from goatee.cli import build_parser, validate_change_id
from goatee.core.parser import parse_change_contract, update_contract_state


class TestCLI(unittest.TestCase):

    def test_validate_change_id_valid(self):
        valid_ids = ["001-export-csv", "fix_bug", "featureA", "v1-0", "123"]
        for cid in valid_ids:
            try:
                validate_change_id(cid)
            except ValueError:
                self.fail(f"validate_change_id raised unexpectedly for valid ID: {cid}")

    def test_validate_change_id_rejects_path_traversal(self):
        invalid_ids = [
            "../escape",
            "foo/bar",
            "\\windows\\path",
            "-leading-hyphen",
            "_leading_underscore",
            "",
            "has spaces",
            "a" * 65,  # Exceeds 64 chars
        ]
        for cid in invalid_ids:
            with self.assertRaises(ValueError, msg=f"Should have rejected invalid ID: {cid}"):
                validate_change_id(cid)

    def test_init_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            
            # Switch cwd to tmpdir for test
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                args = parser.parse_args(["init"])
                rc = args.func(args)
                self.assertEqual(rc, 0)
                
                project_file = p / ".goatee" / "project.md"
                self.assertTrue(project_file.is_file())
                content = project_file.read_text(encoding="utf-8")
                self.assertIn(f"Project-Name: {p.name}", content)
                self.assertIn("Permanent Constraints", content)

                # Refuse if already exists
                rc2 = args.func(args)
                self.assertEqual(rc2, 1)
            finally:
                os.chdir(orig_cwd)

    def test_new_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                # First init
                args_init = parser.parse_args(["init"])
                args_init.func(args_init)

                # Create new change
                args_new = parser.parse_args(["new", "001-export-csv"])
                rc = args_new.func(args_new)
                self.assertEqual(rc, 0)

                change_file = p / ".goatee" / "changes" / "001-export-csv" / "change.md"
                self.assertTrue(change_file.is_file())
                
                contract = parse_change_contract(change_file)
                self.assertEqual(contract.contract_state.agreement_status, "DRAFT")
                self.assertEqual(contract.contract_state.budget_status, "DRAFT")
                self.assertIsNone(contract.contract_state.baseline_commit)

                # Refuse duplicate
                rc_dup = args_new.func(args_new)
                self.assertEqual(rc_dup, 1)

                # Refuse path traversal
                args_invalid = parser.parse_args(["new", "../escaped"])
                rc_invalid = args_invalid.func(args_invalid)
                self.assertEqual(rc_invalid, 1)
            finally:
                os.chdir(orig_cwd)

    def test_approve_command_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                # 1. Initialize git repo with an initial commit
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@goatee.local"], check=True)
                subprocess.run(["git", "config", "user.name", "GoATee Test"], check=True)
                (p / "initial.txt").write_text("initial app code", encoding="utf-8")
                subprocess.run(["git", "add", "initial.txt"], check=True)
                subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)
                
                head_sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

                # 2. Initialize GoATee and create new change
                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "002-feature"]).func(parser.parse_args(["new", "002-feature"]))
                change_file = p / ".goatee" / "changes" / "002-feature" / "change.md"

                # 3. Approve fails while Agreement-Status is DRAFT
                rc_draft = parser.parse_args(["approve", "002-feature"]).func(parser.parse_args(["approve", "002-feature"]))
                self.assertEqual(rc_draft, 1)

                # 4. Set Agreement-Status to APPROVED, Budget-Status still DRAFT -> fails
                update_contract_state(change_file, "Agreement-Status", "APPROVED")
                rc_half = parser.parse_args(["approve", "002-feature"]).func(parser.parse_args(["approve", "002-feature"]))
                self.assertEqual(rc_half, 1)

                # 5. Set Budget-Status to APPROVED
                update_contract_state(change_file, "Budget-Status", "APPROVED")

                # 6. Uncommitted application-code change blocks approve
                (p / "app.py").write_text("dirty change", encoding="utf-8")
                rc_dirty = parser.parse_args(["approve", "002-feature"]).func(parser.parse_args(["approve", "002-feature"]))
                self.assertEqual(rc_dirty, 1)
                (p / "app.py").unlink()

                # 7. Approve succeeds when app is clean
                rc_clean = parser.parse_args(["approve", "002-feature"]).func(parser.parse_args(["approve", "002-feature"]))
                self.assertEqual(rc_clean, 0)

                contract = parse_change_contract(change_file)
                self.assertEqual(contract.contract_state.baseline_commit, head_sha)

                # 8. Re-approving an already baselined change is blocked
                rc_already = parser.parse_args(["approve", "002-feature"]).func(parser.parse_args(["approve", "002-feature"]))
                self.assertEqual(rc_already, 1)
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
