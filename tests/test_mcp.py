"""Unit tests for pure standard library Model Context Protocol (MCP) stdio server."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from goatee.core.mcp import process_mcp_message, run_mcp_server


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_root, check=True)

        (self.repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_root, check=True, capture_output=True)

        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        # Initialize GoATee project
        goatee_dir = self.repo_root / ".goatee"
        goatee_dir.mkdir()
        (goatee_dir / "project.md").write_text("Project-Name: TestProj\n", encoding="utf-8")

        # Create change
        change_dir = goatee_dir / "changes" / "001-mcp"
        change_dir.mkdir(parents=True)
        self.change_dir = change_dir

        contract_content = f"""# Change: 001-mcp

## Intent
Test MCP server tool calls.

## Agreed Scope
### In Scope
- Work

### Out of Scope
- None

## Acceptance Criteria
- AC-01: Automated check
  Evidence:
    type: automated
    command: python3 -c "print('ok')"
    expected: exit 0

## Build Budget
### Expected Surface
- src/app.py

### Reuse Strategy
- None

Dependency-Policy: No new runtime dependencies

### Blast Radius
- None

## Contract State
Agreement-Status: APPROVED
Budget-Status: APPROVED
Baseline-Commit: {self.baseline_sha}
"""
        (change_dir / "change.md").write_text(contract_content, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_initialize_handshake(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "goatee")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 2)
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("goatee_get_contract", tool_names)
        self.assertIn("goatee_check_budget", tool_names)
        self.assertIn("goatee_get_status", tool_names)
        self.assertIn("goatee_propose_budget", tool_names)
        self.assertIn("goatee_lint_contract", tool_names)

    def test_call_get_contract(self):
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "goatee_get_contract",
                "arguments": {"change_id": "001-mcp"}
            }
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 3)
        content_text = resp["result"]["content"][0]["text"]
        data = json.loads(content_text)
        self.assertEqual(data["change_id"], "001-mcp")
        self.assertEqual(data["agreement_status"], "APPROVED")
        self.assertIn("src/app.py", data["build_budget"]["expected_surface"])

    def test_call_check_budget(self):
        # Create unapproved file to trigger deviation
        (self.repo_root / "deviant.py").write_text("# dev\n", encoding="utf-8")

        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "goatee_check_budget",
                "arguments": {"change_id": "001-mcp"}
            }
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 4)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("Pre-Flight Budget Check: FAILED", resp["result"]["content"][0]["text"])
        self.assertIn("deviant.py", resp["result"]["content"][0]["text"])

    def test_run_mcp_server_stdio(self):
        input_lines = [
            json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"}),
            json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/list"}),
        ]
        stdin = io.StringIO("\n".join(input_lines) + "\n")
        stdout = io.StringIO()

        ret = run_mcp_server(stdin=stdin, stdout=stdout, repo_root=self.repo_root)
        self.assertEqual(ret, 0)

        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        resp1 = json.loads(lines[0])
        resp2 = json.loads(lines[1])
        self.assertEqual(resp1["id"], 10)
        self.assertEqual(resp2["id"], 11)

    def test_propose_budget_success_for_draft(self):
        draft_dir = self.repo_root / ".goatee" / "changes" / "002-draft"
        draft_dir.mkdir(parents=True)
        draft_content = f"""# Change: 002-draft

## Intent
Draft test.

## Agreed Scope
### In Scope
- Work

### Out of Scope
- None

## Acceptance Criteria
- AC-01: Check
  Evidence:
    type: automated
    command: echo ok
    expected: exit 0

## Build Budget
### Expected Surface
- (none)

### Reuse Strategy
- (none)

Dependency-Policy: No new runtime dependencies

### Blast Radius
- (none)

## Contract State
Agreement-Status: DRAFT
Budget-Status: DRAFT
Baseline-Commit: 
"""
        (draft_dir / "change.md").write_text(draft_content, encoding="utf-8")

        req = {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "goatee_propose_budget",
                "arguments": {
                    "change_id": "002-draft",
                    "expected_surface": ["src/new_feature.py", "tests/test_new.py"],
                    "reuse_strategy": ["Existing auth helper"],
                    "blast_radius": ["sensitive/keys.py"]
                }
            }
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertFalse(resp["result"]["isError"])
        self.assertIn("Proposed Build Budget updated", resp["result"]["content"][0]["text"])

        updated_contract = (draft_dir / "change.md").read_text(encoding="utf-8")
        self.assertIn("- src/new_feature.py", updated_contract)
        self.assertIn("- tests/test_new.py", updated_contract)
        self.assertIn("- sensitive/keys.py", updated_contract)

    def test_propose_budget_refused_when_approved(self):
        req = {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "goatee_propose_budget",
                "arguments": {
                    "change_id": "001-mcp",
                    "expected_surface": ["src/rogue.py"]
                }
            }
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("already has an APPROVED budget", resp["result"]["content"][0]["text"])

    def test_call_lint_contract_clean(self):
        req = {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "goatee_lint_contract",
                "arguments": {"change_id": "001-mcp"}
            }
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertFalse(resp["result"]["isError"])
        self.assertIn("001-mcp", resp["result"]["content"][0]["text"])
        self.assertIn("Status: PASSED", resp["result"]["content"][0]["text"])

    def test_call_lint_contract_with_errors(self):
        bad_dir = self.repo_root / ".goatee" / "changes" / "003-bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "change.md").write_text("# Change: 003-bad\nMissing sections\n", encoding="utf-8")

        req = {
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {
                "name": "goatee_lint_contract",
                "arguments": {"change_id": "003-bad"}
            }
        }
        resp = process_mcp_message(req, self.repo_root)
        self.assertIsNotNone(resp)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("Status: FAILED", resp["result"]["content"][0]["text"])
        self.assertIn("Missing '## Intent' section", resp["result"]["content"][0]["text"])

    def test_mcp_structured_budget_and_contract(self):
        """AC-07: MCP tools provide backward-compatible structured budget results and enriched contract handoff rules."""
        # 1. Test goatee_get_contract with enriched agent rules and lifecycle hints
        req_contract = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {
                "name": "goatee_get_contract",
                "arguments": {"change_id": "001-mcp"}
            }
        }
        resp_c = process_mcp_message(req_contract, self.repo_root)
        self.assertIsNotNone(resp_c)
        self.assertFalse(resp_c["result"]["isError"])
        contract_data = json.loads(resp_c["result"]["content"][0]["text"])

        # Check enriched agent handoff fields
        self.assertIn("project_constraints", contract_data)
        self.assertIsInstance(contract_data["project_constraints"], list)
        self.assertIn("implementation_rules", contract_data)
        self.assertIsInstance(contract_data["implementation_rules"], list)
        self.assertTrue(len(contract_data["implementation_rules"]) >= 4)
        self.assertIn("Stay strictly within the approved agreement and scope.", contract_data["implementation_rules"])
        self.assertIn("next_action", contract_data)
        self.assertIn("next_action_category", contract_data)
        self.assertEqual(contract_data["next_action_category"], "Machine Action")

        # 2. Test goatee_check_budget structured and backward-compatible content items
        req_budget = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {
                "name": "goatee_check_budget",
                "arguments": {"change_id": "001-mcp"}
            }
        }
        resp_b = process_mcp_message(req_budget, self.repo_root)
        self.assertIsNotNone(resp_b)
        self.assertFalse(resp_b["result"]["isError"])
        contents = resp_b["result"]["content"]
        self.assertEqual(len(contents), 2)
        # Content item 0 is human/agent readable summary
        self.assertEqual(contents[0]["type"], "text")
        self.assertIn("Pre-Flight Budget Check: PASSED", contents[0]["text"])
        # Content item 1 is structured JSON
        self.assertEqual(contents[1]["type"], "text")
        budget_data = json.loads(contents[1]["text"])
        self.assertEqual(budget_data["change_id"], "001-mcp")
        self.assertEqual(budget_data["baseline_commit"], self.baseline_sha)
        self.assertTrue(budget_data["passed"])
        self.assertEqual(budget_data["surface_deviations"], [])
        self.assertEqual(budget_data["protected_changes"], [])

        # 3. Test standardized error with recovery suggestion
        req_bad = {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "goatee_check_budget",
                "arguments": {"change_id": "nonexistent-999"}
            }
        }
        resp_bad = process_mcp_message(req_bad, self.repo_root)
        self.assertIsNotNone(resp_bad)
        self.assertTrue(resp_bad["result"]["isError"])
        self.assertIn("Recovery Suggestion:", resp_bad["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
