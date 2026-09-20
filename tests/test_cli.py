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

    def test_handoff_command_success(self):
        """AC-01: An approved change can be rendered as a portable handoff to stdout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                # Setup git & contract
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "app.txt").write_text("code", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-csv"]).func(parser.parse_args(["new", "001-csv"]))
                c_file = p / ".goatee" / "changes" / "001-csv" / "change.md"
                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                parser.parse_args(["approve", "001-csv"]).func(parser.parse_args(["approve", "001-csv"]))

                # Capture stdout
                import io
                from unittest.mock import patch

                with patch("sys.stdout", new=io.StringIO()) as fake_out:
                    rc = parser.parse_args(["handoff", "001-csv"]).func(parser.parse_args(["handoff", "001-csv"]))
                    output = fake_out.getvalue()

                self.assertEqual(rc, 0)
                self.assertIn("GOATEE HANDOFF", output)
                self.assertIn("Change: 001-csv", output)
                self.assertIn("Baseline-Commit:", output)
                self.assertIn("ACCEPTANCE CRITERIA", output)
                self.assertIn("APPROVED BUILD BUDGET", output)
                self.assertIn("IMPLEMENTATION RULES", output)
            finally:
                os.chdir(orig_cwd)

    def test_handoff_creates_no_persistent_artifact(self):
        """AC-02: handoff creates no persistent artifact on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "app.txt").write_text("code", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-csv"]).func(parser.parse_args(["new", "001-csv"]))
                c_file = p / ".goatee" / "changes" / "001-csv" / "change.md"
                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                parser.parse_args(["approve", "001-csv"]).func(parser.parse_args(["approve", "001-csv"]))

                # Snapshot all files and mod times before handoff
                def get_dir_snapshot(dir_path: Path):
                    return sorted([str(f.relative_to(dir_path)) for f in dir_path.rglob("*")])

                files_before = get_dir_snapshot(p)

                # Run handoff
                import io
                from unittest.mock import patch
                with patch("sys.stdout", new=io.StringIO()):
                    rc = parser.parse_args(["handoff", "001-csv"]).func(parser.parse_args(["handoff", "001-csv"]))

                self.assertEqual(rc, 0)
                files_after = get_dir_snapshot(p)
                self.assertEqual(files_before, files_after)
            finally:
                os.chdir(orig_cwd)

    def test_handoff_refuses_draft(self):
        """AC-03: handoff refuses a DRAFT agreement or budget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-csv"]).func(parser.parse_args(["new", "001-csv"]))

                # Agreement and budget are DRAFT
                rc = parser.parse_args(["handoff", "001-csv"]).func(parser.parse_args(["handoff", "001-csv"]))
                self.assertEqual(rc, 1)

                # Agreement APPROVED but budget DRAFT
                c_file = p / ".goatee" / "changes" / "001-csv" / "change.md"
                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                rc_half = parser.parse_args(["handoff", "001-csv"]).func(parser.parse_args(["handoff", "001-csv"]))
                self.assertEqual(rc_half, 1)
            finally:
                os.chdir(orig_cwd)

    def test_handoff_refuses_missing_baseline(self):
        """AC-04: handoff refuses a change without Baseline-Commit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-csv"]).func(parser.parse_args(["new", "001-csv"]))
                c_file = p / ".goatee" / "changes" / "001-csv" / "change.md"
                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                # Baseline is still missing

                rc = parser.parse_args(["handoff", "001-csv"]).func(parser.parse_args(["handoff", "001-csv"]))
                self.assertEqual(rc, 1)
            finally:
                os.chdir(orig_cwd)

    def test_verify_blocks_dirty_worktree_cases(self):
        """AC-01: Verification blocks if working tree contains any uncommitted Git-visible changes.
        Tests:
        1. uncommitted application file blocks
        2. uncommitted change.md blocks
        3. untracked file in change directory blocks
        4. completely clean repository proceeds
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "code.py").write_text("print('test')", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                c_file = p / ".goatee" / "changes" / "001-feat" / "change.md"

                # Setup simple passing criterion
                content = c_file.read_text(encoding="utf-8")
                simple_ac = (
                    "## Acceptance Criteria\n\n"
                    "- AC-01: Exit check\n"
                    "  Evidence:\n"
                    "    type: automated\n"
                    "    command: python3 -c 'exit(0)'\n"
                    "    expected: exit 0\n\n"
                )
                content = content.replace("## Acceptance Criteria\n\n- AC-01: <description>\n  Evidence:\n    type: automated\n    command: <test command>\n    expected: exit 0\n\n- AC-02: <description>\n  Evidence:\n    type: manual\n    instruction: <what to observe>\n\n", simple_ac)
                c_file.write_text(content, encoding="utf-8")

                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                parser.parse_args(["approve", "001-feat"]).func(parser.parse_args(["approve", "001-feat"]))

                # CASE 2: change.md was modified by approve, so it is uncommitted!
                # Must block verification because governing contract is uncommitted!
                rc_contract_dirty = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc_contract_dirty, 1)

                # Now commit change.md
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "Commit approved contract"], check=True, capture_output=True)

                # CASE 1: uncommitted application file blocks verification
                (p / "dirty_app.py").write_text("x = 1", encoding="utf-8")
                rc_app_dirty = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc_app_dirty, 1)
                (p / "dirty_app.py").unlink()

                # CASE 3: untracked file inside change directory blocks verification
                change_dir = p / ".goatee" / "changes" / "001-feat"
                (change_dir / "stray.txt").write_text("stray file", encoding="utf-8")
                rc_change_untracked = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc_change_untracked, 1)
                (change_dir / "stray.txt").unlink()

                # CASE 4: completely clean repository proceeds to verification
                rc_clean = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc_clean, 0)
            finally:
                os.chdir(orig_cwd)

    def test_verify_blocks_unapproved_or_missing_baseline(self):
        """AC-02: Verification blocks if change has no Baseline-Commit or is in DRAFT."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "code.py").write_text("print('test')", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "scaffold"], check=True, capture_output=True)

                # Change is still in DRAFT
                rc = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc, 1)
            finally:
                os.chdir(orig_cwd)

    def test_verify_generates_receipt_md(self):
        """AC-07: Verification generates complete receipt.md with bounded diagnostics and verdict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            parser = build_parser()
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "code.py").write_text("print('test')", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                c_file = p / ".goatee" / "changes" / "001-feat" / "change.md"

                # Setup simple passing criterion in change.md
                content = c_file.read_text(encoding="utf-8")
                simple_ac = (
                    "## Acceptance Criteria\n\n"
                    "- AC-01: Simple exit check\n"
                    "  Evidence:\n"
                    "    type: automated\n"
                    "    command: python3 -c 'exit(0)'\n"
                    "    expected: exit 0\n\n"
                )
                content = content.replace("## Acceptance Criteria\n\n- AC-01: <description>\n  Evidence:\n    type: automated\n    command: <test command>\n    expected: exit 0\n\n- AC-02: <description>\n  Evidence:\n    type: manual\n    instruction: <what to observe>\n\n", simple_ac)
                c_file.write_text(content, encoding="utf-8")

                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                parser.parse_args(["approve", "001-feat"]).func(parser.parse_args(["approve", "001-feat"]))

                # Commit everything so worktree is clean
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "Deliver change and contract"], check=True, capture_output=True)

                # Execute verify
                rc = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                self.assertEqual(rc, 0)

                receipt_file = p / ".goatee" / "changes" / "001-feat" / "receipt.md"
                self.assertTrue(receipt_file.is_file())
                receipt_text = receipt_file.read_text(encoding="utf-8")
                self.assertIn("Delivery Receipt: 001-feat", receipt_text)
                self.assertIn("Verdict: PASS", receipt_text)
                self.assertIn("Baseline-Commit:", receipt_text)
                self.assertIn("Verified-Commit:", receipt_text)
                self.assertIn("AC-01", receipt_text)
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
