"""Unit tests for the GoATee CLI commands: init, new, approve."""

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import goatee.core.verifier
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

    def test_git_inspection_fails_closed(self):
        """AC-04: Git inspection operations fail closed across initial resolution, diff discovery, and in-flight checks."""
        parser = build_parser()

        # Subcase A: Initial HEAD resolution failure
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                # Repo has no commits, so git rev-parse HEAD fails
                c_dir = p / ".goatee" / "changes" / "001-feat"
                c_dir.mkdir(parents=True)
                (c_dir / "change.md").write_text("dummy", encoding="utf-8")

                stderr_capture = io.StringIO()
                with patch("sys.stderr", stderr_capture):
                    rc = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))

                self.assertNotEqual(rc, 0)
                err_output = stderr_capture.getvalue()
                self.assertIn("GIT INSPECTION FAILED", err_output)
                self.assertFalse((c_dir / "receipt.md").exists())
            finally:
                os.chdir(orig_cwd)

        # Subcase B: Budget diff discovery failure
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "app.py").write_text("x = 1", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                c_file = p / ".goatee" / "changes" / "001-feat" / "change.md"

                content = c_file.read_text(encoding="utf-8")
                simple_ac = (
                    "## Acceptance Criteria\n\n"
                    "- AC-01: Check\n"
                    "  Evidence:\n"
                    "    type: automated\n"
                    "    command: python3 -c 'exit(0)'\n"
                    "    expected: exit 0\n\n"
                )
                content = content.replace("## Acceptance Criteria\n\n- AC-01: <description>\n  Evidence:\n    type: automated\n    command: <test command>\n    expected: exit 0\n\n- AC-02: <description>\n  Evidence:\n    type: manual\n    instruction: <what to observe>\n\n", simple_ac)
                c_file.write_text(content, encoding="utf-8")
                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                # Intentionally set an invalid non-existent baseline commit so git diff fails
                update_contract_state(c_file, "Baseline-Commit", "deadbeef00000000000000000000000000000000")

                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "deliver"], check=True, capture_output=True)

                stderr_capture = io.StringIO()
                with patch("sys.stderr", stderr_capture):
                    rc = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))

                self.assertNotEqual(rc, 0)
                err_output = stderr_capture.getvalue()
                self.assertIn("GIT INSPECTION FAILED", err_output)
                self.assertFalse((p / ".goatee" / "changes" / "001-feat" / "receipt.md").exists())
            finally:
                os.chdir(orig_cwd)

        # Subcase C: In-flight Git inspection failure
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "app.py").write_text("x = 1", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                c_file = p / ".goatee" / "changes" / "001-feat" / "change.md"

                content = c_file.read_text(encoding="utf-8")
                simple_ac = (
                    "## Acceptance Criteria\n\n"
                    "- AC-01: In-flight fail\n"
                    "  Evidence:\n"
                    "    type: automated\n"
                    "    command: python3 -c 'exit(0)'\n"
                    "    expected: exit 0\n\n"
                    "- AC-02: Next check\n"
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

                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "deliver"], check=True, capture_output=True)

                from goatee.core.budget import GitInspectionError
                orig_fp = goatee.core.verifier.get_git_visible_fingerprint
                call_count = [0]
                def mocked_fp(cwd=None):
                    call_count[0] += 1
                    if call_count[0] == 2:  # Post-execution of AC-01
                        raise GitInspectionError("Simulated in-flight git corruption")
                    return orig_fp(cwd)

                with patch("goatee.core.verifier.get_git_visible_fingerprint", side_effect=mocked_fp):
                    rc = parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))

                self.assertEqual(rc, 1)  # NEEDS_REVIEW exits 1
                receipt_file = p / ".goatee" / "changes" / "001-feat" / "receipt.md"
                self.assertTrue(receipt_file.is_file())
                receipt_text = receipt_file.read_text(encoding="utf-8")
                self.assertIn("Verdict: NEEDS_REVIEW", receipt_text)
                self.assertIn("GIT INSPECTION FAILED", receipt_text)
                self.assertIn("AC-01", receipt_text)
                self.assertIn("FAILED", receipt_text)
                self.assertIn("NOT_RUN", receipt_text)
            finally:
                os.chdir(orig_cwd)

    def test_cli_syntax_error_clean_exit(self):
        """Test that malformed contract syntax results in concise stderr and exit 1 with no traceback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            parser = build_parser()
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "init.txt").write_text("hello", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-bad"]).func(parser.parse_args(["new", "001-bad"]))
                c_file = p / ".goatee" / "changes" / "001-bad" / "change.md"

                # Corrupt change.md: remove Contract State
                content = c_file.read_text(encoding="utf-8").split("## Contract State")[0]
                c_file.write_text(content, encoding="utf-8")

                stderr_capture = io.StringIO()
                with patch("sys.stderr", stderr_capture):
                    rc = parser.parse_args(["approve", "001-bad"]).func(parser.parse_args(["approve", "001-bad"]))

                self.assertEqual(rc, 1)
                err_output = stderr_capture.getvalue()
                self.assertIn("Contract syntax error:", err_output)
                self.assertIn("Missing required section: '## Contract State'", err_output)
                self.assertNotIn("Traceback", err_output)
            finally:
                os.chdir(orig_cwd)

    def test_status_cli_error_handling(self):
        """AC-05: Outside-project and missing-change errors are clean, non-zero, and traceback-free."""
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                # 1. Outside project
                stderr_out = io.StringIO()
                with patch("sys.stderr", stderr_out):
                    rc = parser.parse_args(["status"]).func(parser.parse_args(["status"]))
                self.assertEqual(rc, 1)
                self.assertIn("Error: Not a GoATee project", stderr_out.getvalue())
                self.assertNotIn("Traceback", stderr_out.getvalue())

                # Initialize project
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "t@test.com"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (Path(tmpdir) / "f.txt").write_text("ok", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)
                parser.parse_args(["init"]).func(parser.parse_args(["init"]))

                # 2. Missing change ID
                stderr_out = io.StringIO()
                with patch("sys.stderr", stderr_out):
                    rc = parser.parse_args(["status", "999-missing"]).func(parser.parse_args(["status", "999-missing"]))
                self.assertEqual(rc, 1)
                self.assertIn("Error: Change '999-missing' not found.", stderr_out.getvalue())
                self.assertNotIn("Traceback", stderr_out.getvalue())

                # 3. Path traversal / invalid change ID
                stderr_out = io.StringIO()
                with patch("sys.stderr", stderr_out):
                    rc = parser.parse_args(["status", "../bad"]).func(parser.parse_args(["status", "../bad"]))
                self.assertEqual(rc, 1)
                self.assertIn("Error: Invalid change-id '../bad'", stderr_out.getvalue())
                self.assertNotIn("Traceback", stderr_out.getvalue())
            finally:
                os.chdir(orig_cwd)

    def test_status_zero_side_effects(self):
        """AC-07: status and status <id> have zero filesystem/Git side effects on clean and dirty repos."""
        import hashlib
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "t@test.com"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "app.py").write_text("x = 1\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "add 001-feat"], check=True, capture_output=True)

                def get_tree_snapshot():
                    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
                    porcelain = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
                    index_diff = subprocess.run(["git", "diff", "--cached", "--binary"], capture_output=True).stdout
                    ls_stage = subprocess.run(["git", "ls-files", "--stage"], capture_output=True, text=True).stdout
                    file_hashes = {}
                    for root, _, files in os.walk(p):
                        if ".git" in root:
                            continue
                        for f in files:
                            fpath = Path(root) / f
                            file_hashes[str(fpath.relative_to(p))] = hashlib.sha256(fpath.read_bytes()).hexdigest()
                    return {
                        "head": head,
                        "porcelain": porcelain,
                        "index_diff": hashlib.sha256(index_diff).hexdigest(),
                        "ls_stage": ls_stage,
                        "file_hashes": file_hashes,
                    }

                # --- Subcase A: CLEAN repository ---
                snap_clean_before = get_tree_snapshot()

                stdout_clean_all = io.StringIO()
                with patch("sys.stdout", stdout_clean_all):
                    rc = parser.parse_args(["status"]).func(parser.parse_args(["status"]))
                self.assertEqual(rc, 0)
                self.assertIn("Working Tree: CLEAN", stdout_clean_all.getvalue())

                stdout_clean_detail = io.StringIO()
                with patch("sys.stdout", stdout_clean_detail):
                    rc = parser.parse_args(["status", "001-feat"]).func(parser.parse_args(["status", "001-feat"]))
                self.assertEqual(rc, 0)
                self.assertIn("Working Tree: CLEAN", stdout_clean_detail.getvalue())

                snap_clean_after = get_tree_snapshot()
                self.assertEqual(snap_clean_before, snap_clean_after)

                # --- Subcase B: Deliberately DIRTY repository ---
                # 1. Modify tracked file and stage it
                (p / "app.py").write_text("x = 2 (dirty modified)\n", encoding="utf-8")
                subprocess.run(["git", "add", "app.py"], check=True)
                # 2. Create untracked file
                (p / "untracked.txt").write_text("dirty untracked\n", encoding="utf-8")

                snap_dirty_before = get_tree_snapshot()
                self.assertTrue(len(snap_dirty_before["porcelain"].strip()) > 0)

                stdout_dirty_all = io.StringIO()
                with patch("sys.stdout", stdout_dirty_all):
                    rc = parser.parse_args(["status"]).func(parser.parse_args(["status"]))
                self.assertEqual(rc, 0)
                self.assertIn("Working Tree: DIRTY", stdout_dirty_all.getvalue())

                stdout_dirty_detail = io.StringIO()
                with patch("sys.stdout", stdout_dirty_detail):
                    rc = parser.parse_args(["status", "001-feat"]).func(parser.parse_args(["status", "001-feat"]))
                self.assertEqual(rc, 0)
                self.assertIn("Working Tree: DIRTY", stdout_dirty_detail.getvalue())

                snap_dirty_after = get_tree_snapshot()
                self.assertEqual(snap_dirty_before, snap_dirty_after)
                # Specifically verify index diff hash and ls-files were completely unchanged
                self.assertEqual(snap_dirty_before["index_diff"], snap_dirty_after["index_diff"])
                self.assertEqual(snap_dirty_before["ls_stage"], snap_dirty_after["ls_stage"])
                self.assertEqual((p / "app.py").read_text(encoding="utf-8"), "x = 2 (dirty modified)\n")
                self.assertEqual((p / "untracked.txt").read_text(encoding="utf-8"), "dirty untracked\n")
            finally:
                os.chdir(orig_cwd)

    def test_attest_cli_validation_and_errors(self):
        """AC-09: CLI argument validation, note normalization/escaping, and fail-closed error handling without tracebacks."""
        parser = build_parser()
        orig_cwd = os.getcwd()

        # 1. Flag validation: missing mutual exclusion action
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", io.StringIO()):
                parser.parse_args(["attest", "001-feat", "--criterion", "AC-01", "--note", "test"])
        self.assertNotEqual(cm.exception.code, 0)

        # 2. Flag validation: both --verified and --rejected
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", io.StringIO()):
                parser.parse_args(["attest", "001-feat", "--criterion", "AC-01", "--verified", "--rejected", "--note", "test"])
        self.assertNotEqual(cm.exception.code, 0)

        # 3. Flag validation: missing --note
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", io.StringIO()):
                parser.parse_args(["attest", "001-feat", "--criterion", "AC-01", "--verified"])
        self.assertNotEqual(cm.exception.code, 0)

        # 4. --verified without --criterion -> exits 1 with clean error
        stderr_io = io.StringIO()
        with patch("sys.stderr", stderr_io):
            rc = parser.parse_args(["attest", "001-feat", "--verified", "--note", "ok"]).func(
                parser.parse_args(["attest", "001-feat", "--verified", "--note", "ok"])
            )
        self.assertEqual(rc, 1)
        self.assertIn("Error: --verified requires --criterion.", stderr_io.getvalue())
        self.assertNotIn("Traceback", stderr_io.getvalue())

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)
                (p / "README.md").write_text("# Test\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-feat"]).func(parser.parse_args(["new", "001-feat"]))
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "commit goatee init"], check=True, capture_output=True)

                # 5. Non-existent change error
                stderr_nonexist = io.StringIO()
                with patch("sys.stderr", stderr_nonexist):
                    rc = parser.parse_args(["attest", "999-missing", "--rejected", "--note", "missing"]).func(
                        parser.parse_args(["attest", "999-missing", "--rejected", "--note", "missing"])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Error: Change '999-missing' not found.", stderr_nonexist.getvalue())
                self.assertNotIn("Traceback", stderr_nonexist.getvalue())

                # 6. Unverified change error
                stderr_unver = io.StringIO()
                with patch("sys.stderr", stderr_unver):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "unverified"]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "unverified"])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("has no delivery receipt", stderr_unver.getvalue())
                self.assertNotIn("Traceback", stderr_unver.getvalue())

                # Setup verified change
                c_file = p / ".goatee" / "changes" / "001-feat" / "change.md"
                c_content = c_file.read_text(encoding="utf-8")
                c_content = c_content.replace("command: <test command>", "command: python3 -c 'exit(0)'")
                c_file.write_text(c_content, encoding="utf-8")
                update_contract_state(c_file, "Agreement-Status", "APPROVED")
                update_contract_state(c_file, "Budget-Status", "APPROVED")
                parser.parse_args(["approve", "001-feat"]).func(parser.parse_args(["approve", "001-feat"]))
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", "deliver"], check=True, capture_output=True)
                parser.parse_args(["verify", "001-feat"]).func(parser.parse_args(["verify", "001-feat"]))
                subprocess.run(["git", "add", ".goatee/changes/001-feat/receipt.md"], check=True)
                subprocess.run(["git", "commit", "-m", "receipt"], check=True, capture_output=True)

                # 7. Empty note error
                stderr_empty = io.StringIO()
                with patch("sys.stderr", stderr_empty):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "   "]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "   "])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Error: --note must not be empty.", stderr_empty.getvalue())

                # 8. Multi-line note error
                stderr_multiline = io.StringIO()
                with patch("sys.stderr", stderr_multiline):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "line1\nline2"]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "line1\nline2"])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Error: --note must be a single line without line breaks.", stderr_multiline.getvalue())

                # 9. Dirty worktree error
                (p / "dirty.txt").write_text("dirty\n", encoding="utf-8")
                stderr_dirty = io.StringIO()
                with patch("sys.stderr", stderr_dirty):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "valid"]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "valid"])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Error: Working tree must be clean before recording human attestation.", stderr_dirty.getvalue())
                (p / "dirty.txt").unlink()

                # 9b. Reviewer identity with embedded newline fails closed before review.md creation
                subprocess.run(["git", "config", "user.name", "Tester\nInjected: line"], check=True)
                stderr_inj_name = io.StringIO()
                with patch("sys.stderr", stderr_inj_name):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "valid"]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "valid"])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Error: Git user.name and user.email must contain single-line values before attesting.", stderr_inj_name.getvalue())
                self.assertFalse((p / ".goatee" / "changes" / "001-feat" / "review.md").exists())
                subprocess.run(["git", "config", "user.name", "Tester"], check=True)

                subprocess.run(["git", "config", "user.email", "test@test.local\nInjected: line"], check=True)
                stderr_inj_email = io.StringIO()
                with patch("sys.stderr", stderr_inj_email):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "valid"]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", "valid"])
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Error: Git user.name and user.email must contain single-line values before attesting.", stderr_inj_email.getvalue())
                self.assertFalse((p / ".goatee" / "changes" / "001-feat" / "review.md").exists())
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)

                # 10. Success with note containing pipe and backslash (escaping test)
                stdout_ok = io.StringIO()
                with patch("sys.stdout", stdout_ok):
                    rc = parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", r"Verified with | pipe and \ backslash"]).func(
                        parser.parse_args(["attest", "001-feat", "--criterion", "AC-02", "--verified", "--note", r"Verified with | pipe and \ backslash"])
                    )
                self.assertEqual(rc, 0)
                self.assertIn("Recorded VERIFIED attestation for criterion 'AC-02'", stdout_ok.getvalue())

                # Verify review.md contains properly escaped table row and raw event note
                review_content = (p / ".goatee" / "changes" / "001-feat" / "review.md").read_text(encoding="utf-8")
                self.assertIn(r"Verified with \| pipe and \\ backslash", review_content)
                self.assertIn(r"Note: Verified with | pipe and \ backslash", review_content)

            finally:
                os.chdir(orig_cwd)

    def test_active_change_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                subprocess.run(["git", "init"], check=True, capture_output=True)
                subprocess.run(["git", "config", "user.name", "Test User"], check=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], check=True)
                (p / "README.md").write_text("# Test\n", encoding="utf-8")
                subprocess.run(["git", "add", "README.md"], check=True)
                subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)

                parser = build_parser()
                parser.parse_args(["init"]).func(parser.parse_args(["init"]))
                parser.parse_args(["new", "001-auto"]).func(parser.parse_args(["new", "001-auto"]))

                from goatee.core.status import resolve_active_change_id
                # 1. Single unverified change in repo
                resolved = resolve_active_change_id(p)
                self.assertEqual(resolved, "001-auto")

                # 2. Test branch name inference
                subprocess.run(["git", "checkout", "-b", "change/001-auto"], check=True, capture_output=True)
                resolved_branch = resolve_active_change_id(p)
                self.assertEqual(resolved_branch, "001-auto")

                # 3. Test .goatee/.active file pointer
                (p / ".goatee" / ".active").write_text("001-auto\n", encoding="utf-8")
                resolved_ptr = resolve_active_change_id(p)
                self.assertEqual(resolved_ptr, "001-auto")
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
