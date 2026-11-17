"""Unit tests for GoATee HTML delivery certificate exporter."""

import tempfile
import unittest
from pathlib import Path
from goatee.core.exporter import generate_html_certificate, export_certificate
from goatee.core.models import AcceptanceCriterion, BuildBudget, ChangeContract, ContractState
from goatee.core.status import ChangeDetailReport, ReceiptRow, AttestationRow


class TestExporter(unittest.TestCase):

    def test_generate_html_certificate_structure(self):
        report = ChangeDetailReport(
            change_id="001-export",
            intent="Export table data to CSV cleanly.",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit="1234567890abcdef",
            working_tree="CLEAN",
            is_verified=True,
            verified_commit="abcdef1234567890",
            verified_at="2026-11-17 12:00:00Z",
            machine_verdict="PASS",
            human_review="ACCEPTED",
            review_updated_at="2026-11-17 12:05:00Z",
            latest_reviewer="Alice <alice@example.com>",
            receipt_sha256="aabbccddeeff00112233445566778899",
            expected_changes=["src/export.py", "tests/test_export.py"],
            surface_deviations=[],
            protected_changes=[],
            criteria_statuses=[
                ReceiptRow(
                    criterion_id="AC-01",
                    criterion_type="automated",
                    declared="pytest tests/test_export.py",
                    observed="exit 0 (0.42s)",
                    status="VERIFIED"
                )
            ]
        )

        html_out = generate_html_certificate(report, project_name="AnalyticsApp")
        self.assertIn("<!DOCTYPE html>", html_out)
        self.assertIn("GoATee Delivery &amp; Compliance Certificate", html_out)
        self.assertIn("001-export", html_out)
        self.assertIn("AnalyticsApp", html_out)
        self.assertIn("Export table data to CSV cleanly.", html_out)
        self.assertIn("AC-01", html_out)
        self.assertIn("pytest tests/test_export.py", html_out)
        self.assertIn("VERIFIED", html_out)
        self.assertIn("Alice &lt;alice@example.com&gt;", html_out)
        self.assertIn("src/export.py", html_out)
        self.assertIn("Zero unapproved surface deviations", html_out)
        self.assertIn("Zero blast-radius breaches", html_out)

    def test_html_escaping_prevents_xss(self):
        report = ChangeDetailReport(
            change_id="001-xss",
            intent="<script>alert('xss')</script>",
            agreement_status="APPROVED",
            budget_status="APPROVED",
            baseline_commit="12345",
            working_tree="CLEAN",
            is_verified=True,
            latest_reviewer="Evil <img src=x onerror=alert(1)>",
            criteria_statuses=[
                ReceiptRow(
                    criterion_id="<AC-XSS>",
                    criterion_type="automated",
                    declared="echo '<script>'",
                    observed="<fail>",
                    status="FAILED"
                )
            ]
        )

        html_out = generate_html_certificate(report, project_name="<TestApp>")
        self.assertNotIn("<script>alert", html_out)
        self.assertNotIn("<img src=x", html_out)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html_out)
        self.assertIn("&lt;TestApp&gt;", html_out)

    def test_export_certificate_file_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goatee_dir = root / ".goatee"
            goatee_dir.mkdir(parents=True)
            (goatee_dir / "project.md").write_text("Project-Name: CertTest\n", encoding="utf-8")

            import subprocess
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tester@test.local"], cwd=root, check=True)
            (root / "README.md").write_text("# Test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)

            head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()

            c_dir = goatee_dir / "changes" / "001-feat"
            c_dir.mkdir(parents=True)
            (c_dir / "change.md").write_text(f"""# Change: 001-feat

## Intent
Test intent.

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
- app.py
### Reuse Strategy
- None
Dependency-Policy: None
### Blast Radius
- config/db.py

## Contract State
Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: {head_sha}
""", encoding="utf-8")

            # Default output path
            cert_path = export_certificate(root, "001-feat")
            self.assertTrue(cert_path.is_file())
            self.assertEqual(cert_path.name, "001-feat-certificate.html")
            content = cert_path.read_text(encoding="utf-8")
            self.assertIn("001-feat", content)
            self.assertIn("CertTest", content)

            # Custom output path
            custom = root / "reports" / "summary.html"
            custom_path = export_certificate(root, "001-feat", output_path=custom)
            self.assertTrue(custom_path.is_file())
            self.assertEqual(custom_path, custom)


if __name__ == "__main__":
    unittest.main()
