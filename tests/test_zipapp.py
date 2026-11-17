"""Unit tests for GoATee standalone executable zipapp packaging."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts.build_zipapp import build_zipapp


class TestZipappPackaging(unittest.TestCase):

    def test_build_and_execute_standalone_zipapp(self):
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            target_bin = Path(tmp) / "goatee"
            built_path = build_zipapp(repo_root=repo_root, output_path=target_bin)
            self.assertTrue(built_path.is_file())
            self.assertEqual(built_path, target_bin)

            # Test execution of standalone binary
            res_ver = subprocess.run([str(built_path), "--version"], capture_output=True, text=True)
            self.assertEqual(res_ver.returncode, 0)
            self.assertIn("goatee 0.3.0", res_ver.stdout)

            res_help = subprocess.run([str(built_path), "--help"], capture_output=True, text=True)
            self.assertEqual(res_help.returncode, 0)
            self.assertIn("GoATee: Spec and delivery control layer", res_help.stdout)
            self.assertIn("export", res_help.stdout)
            self.assertIn("cert", res_help.stdout)


if __name__ == "__main__":
    unittest.main()
