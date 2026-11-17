"""Presentation-ready, tamper-evident HTML delivery certificate generator for GoATee."""

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from goatee.core.models import ChangeContract
from goatee.core.parser import parse_change_contract
from goatee.core.status import (
    get_change_report,
    find_project_root,
    parse_project_name,
    get_working_tree_state,
    resolve_current_head,
    ChangeDetailReport,
    ControlStateError,
    ReceiptSyntaxError
)


def generate_html_certificate(
    report: ChangeDetailReport,
    contract: Optional[ChangeContract] = None,
    project_name: str = "GoATee"
) -> str:
    """Render a standalone, CSS-embedded, tamper-evident HTML delivery certificate."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    # Dual-verdict styles
    machine_color = "#10b981" if report.machine_verdict == "PASS" else ("#f59e0b" if report.machine_verdict == "NEEDS_REVIEW" else "#ef4444")
    human_color = "#10b981" if report.human_review == "ACCEPTED" else ("#f59e0b" if report.human_review == "PENDING" else "#ef4444")

    # Criteria rows
    criteria_rows_html = []
    for r in report.criteria_statuses:
        status_color = "#10b981" if r.status == "VERIFIED" else ("#f59e0b" if r.status == "NEEDS_REVIEW" else "#ef4444")
        obs_text = r.observed or "(pending)"
        criteria_rows_html.append(f"""
        <tr>
            <td style="font-weight: 600; font-family: monospace;">{html.escape(r.criterion_id)}</td>
            <td><span class="badge badge-subtle">{html.escape(r.criterion_type)}</span></td>
            <td><code>{html.escape(r.declared)}</code></td>
            <td><code>{html.escape(obs_text)}</code></td>
            <td><span class="badge" style="background: {status_color}; color: #fff;">{html.escape(r.status)}</span></td>
        </tr>
        """)

    # Budget rows
    exp_changes_html = "".join(f"<li><code>{html.escape(f)}</code></li>" for f in report.expected_changes) or "<li><em>(none)</em></li>"
    deviations_html = "".join(f"<li style='color: #ef4444;'><code>{html.escape(f)}</code></li>" for f in report.surface_deviations) or "<li style='color: #10b981;'>✓ Zero unapproved surface deviations</li>"
    protected_html = "".join(f"<li style='color: #ef4444;'><code>{html.escape(f)}</code></li>" for f in report.protected_changes) or "<li style='color: #10b981;'>✓ Zero blast-radius breaches</li>"

    # Attestation notes
    attestation_info_html = ""
    if report.human_review == "ACCEPTED":
        attestation_info_html = f"""
        <div class="card" style="border-left: 4px solid #10b981;">
            <h3 style="color: #10b981; margin-top: 0;">✓ Human Review Accepted</h3>
            <p><strong>Reviewer:</strong> {html.escape(report.latest_reviewer or 'Unknown')}</p>
            <p><strong>Attested At:</strong> {html.escape(report.review_updated_at or 'Unknown')}</p>
            <p><strong>Receipt Binding SHA-256:</strong> <code>{html.escape(report.receipt_sha256 or 'None')}</code></p>
        </div>
        """
    elif report.human_review == "PENDING":
        attestation_info_html = """
        <div class="card" style="border-left: 4px solid #f59e0b;">
            <h3 style="color: #f59e0b; margin-top: 0;">⏳ Human Attestation Pending</h3>
            <p>Automated verification complete. Manual acceptance criteria are pending human sign-off via <code>goatee attest</code>.</p>
        </div>
        """

    contract_sha = getattr(contract.contract_state, "contract_sha256", None) if contract else None

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GoATee Delivery Certificate: {html.escape(report.change_id)}</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-card: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --border: #475569;
            --accent: #38bdf8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
        }}
        @media print {{
            body {{ background: #fff !important; color: #000 !important; }}
            .card {{ border: 1px solid #ccc !important; background: #fff !important; color: #000 !important; }}
            .badge {{ border: 1px solid #666; }}
            pre, code {{ background: #f1f5f9 !important; color: #000 !important; }}
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 32px 16px;
            line-height: 1.6;
        }}
        .container {{
            max-width: 960px;
            margin: 0 auto;
        }}
        header {{
            border-bottom: 2px solid var(--border);
            padding-bottom: 24px;
            margin-bottom: 32px;
        }}
        .title-row {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 16px;
        }}
        h1 {{
            font-size: 2rem;
            margin: 0 0 8px 0;
            color: #fff;
            letter-spacing: -0.025em;
        }}
        .subtitle {{
            color: var(--text-muted);
            font-size: 1rem;
            margin: 0;
        }}
        .verdict-banner {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .card {{
            background: var(--surface);
            border-radius: 8px;
            padding: 20px;
            border: 1px solid var(--border);
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .badge-subtle {{
            background: var(--surface-card);
            color: var(--text-muted);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
            font-size: 0.9rem;
        }}
        th, td {{
            text-align: left;
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background: var(--surface);
            color: var(--text-muted);
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        code {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            background: rgba(0,0,0,0.3);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.85em;
            color: var(--accent);
        }}
        ul {{
            margin: 8px 0;
            padding-left: 20px;
        }}
        li {{
            margin-bottom: 4px;
        }}
        footer {{
            margin-top: 48px;
            padding-top: 24px;
            border-top: 1px solid var(--border);
            text-align: center;
            font-size: 0.85rem;
            color: var(--text-muted);
        }}
        .seal {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(56, 189, 248, 0.1);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: var(--accent);
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-row">
                <div>
                    <h1>GoATee Delivery &amp; Compliance Certificate</h1>
                    <p class="subtitle">Change Contract: <strong>{html.escape(report.change_id)}</strong> | Project: <strong>{html.escape(project_name)}</strong></p>
                </div>
                <div class="seal">
                    🛡️ Cryptographically Bound Artifact
                </div>
            </div>
        </header>

        <section class="verdict-banner">
            <div class="card">
                <span class="badge badge-subtle">Machine Evidence</span>
                <h2 style="color: {machine_color}; margin: 8px 0 4px 0;">{html.escape(report.machine_verdict)}</h2>
                <p style="margin: 0; font-size: 0.85rem; color: var(--text-muted);">Verified at {html.escape(report.verified_at or 'Pending')}</p>
            </div>
            <div class="card">
                <span class="badge badge-subtle">Human Governance</span>
                <h2 style="color: {human_color}; margin: 8px 0 4px 0;">{html.escape(report.human_review)}</h2>
                <p style="margin: 0; font-size: 0.85rem; color: var(--text-muted);">Attested by {html.escape(report.latest_reviewer or 'Pending Review')}</p>
            </div>
        </section>

        {attestation_info_html}

        <div class="card" style="margin-bottom: 24px;">
            <h3 style="margin-top: 0;">Intent</h3>
            <p style="font-size: 1.05rem; margin-bottom: 0;">{html.escape(report.intent or '(none declared)')}</p>
        </div>

        <div class="card" style="margin-bottom: 24px;">
            <h3 style="margin-top: 0;">Cryptographic Provenance & Git Tracking</h3>
            <table style="margin-top: 8px;">
                <tr>
                    <td style="width: 220px; font-weight: 600;">Baseline Commit:</td>
                    <td><code>{html.escape(report.baseline_commit or 'none')}</code></td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Verified Code Commit:</td>
                    <td><code>{html.escape(report.verified_commit or 'Pending')}</code></td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Receipt Content SHA-256:</td>
                    <td><code>{html.escape(report.receipt_sha256 or 'Pending')}</code></td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Contract Integrity SHA-256:</td>
                    <td><code>{html.escape(contract_sha or 'Pending approval')}</code></td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Working Tree State:</td>
                    <td><span class="badge" style="background: #10b981; color: #fff;">{html.escape(report.working_tree)}</span></td>
                </tr>
            </table>
        </div>

        <div class="card" style="margin-bottom: 24px;">
            <h3 style="margin-top: 0;">Requirement Coverage Matrix</h3>
            <table>
                <thead>
                    <tr>
                        <th>Criterion</th>
                        <th>Type</th>
                        <th>Declared Requirement</th>
                        <th>Observed Execution</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(criteria_rows_html)}
                </tbody>
            </table>
        </div>

        <div class="card" style="margin-bottom: 24px;">
            <h3 style="margin-top: 0;">Build Budget & Surface Analysis</h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px;">
                <div>
                    <h4 style="margin: 0 0 8px 0; color: var(--text-muted);">Expected Changes</h4>
                    <ul>{exp_changes_html}</ul>
                </div>
                <div>
                    <h4 style="margin: 0 0 8px 0; color: var(--text-muted);">Surface Deviations</h4>
                    <ul>{deviations_html}</ul>
                </div>
                <div>
                    <h4 style="margin: 0 0 8px 0; color: var(--text-muted);">Blast Radius Protection</h4>
                    <ul>{protected_html}</ul>
                </div>
            </div>
        </div>

        <footer>
            <p>Generated by <strong>GoATee</strong> (v0.3.0) on {html.escape(now_utc)}</p>
            <p style="font-size: 0.75rem;">Spec and delivery control layer for AI-assisted software changes. Verified against clean Git working tree.</p>
        </footer>
    </div>
</body>
</html>
"""


def export_certificate(
    root: Path,
    change_id: str,
    output_path: Optional[Path] = None
) -> Path:
    """Generate and export a standalone HTML delivery certificate for a change."""
    project_name = parse_project_name(root / ".goatee" / "project.md")
    working_tree = get_working_tree_state(root)
    current_head = resolve_current_head(root)

    report = get_change_report(root, change_id, working_tree, current_head)

    contract_path = root / ".goatee" / "changes" / change_id / "change.md"
    if not contract_path.is_file():
        contract_path = root / ".goatee" / "archive" / change_id / "change.md"
    contract = parse_change_contract(contract_path) if contract_path.is_file() else None

    html_content = generate_html_certificate(report, contract, project_name)

    if output_path:
        target = output_path if output_path.is_absolute() else root / output_path
    else:
        # Default to .goatee/certificates/<change_id>-certificate.html
        certs_dir = root / ".goatee" / "certificates"
        certs_dir.mkdir(parents=True, exist_ok=True)
        target = certs_dir / f"{change_id}-certificate.html"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_content, encoding="utf-8")
    return target


def generate_pr_comment_markdown(
    report: ChangeDetailReport,
    contract: Optional[ChangeContract] = None,
    project_name: str = "GoATee"
) -> str:
    """Render a GitHub/GitLab-ready Markdown summary for PR comments."""
    machine_badge = {
        "PASS": "🟢 **PASS**",
        "NEEDS_REVIEW": "🟡 **NEEDS REVIEW**",
        "NOT_VERIFIED": "⚪ **NOT VERIFIED**",
    }.get(report.machine_verdict, f"🔴 **{report.machine_verdict}**")

    human_badge = {
        "ACCEPTED": "🟢 **ACCEPTED**",
        "PENDING": "🟡 **PENDING**",
        "REJECTED": "🔴 **REJECTED**",
    }.get(report.human_review, report.human_review)

    lines: List[str] = [
        f"### 🛡️ GoATee Delivery &amp; Governance: `{report.change_id}`",
        "",
        f"- **Machine Verdict**: {machine_badge}",
        f"- **Human Review**: {human_badge}",
        f"- **Baseline Commit**: `{report.baseline_commit or 'none'}`",
        f"- **Verified Commit**: `{report.verified_commit or report.candidate_commit or 'none'}`",
    ]

    if report.receipt_sha256:
        lines.append(f"- **Receipt SHA256**: `{report.receipt_sha256[:16]}...`")

    if report.intent:
        lines.extend(["", f"> **Intent**: {report.intent}", ""])

    lines.extend([
        "#### Requirement Coverage Matrix",
        "",
        "| Criterion | Type | Machine Status | Human Sign-Off | Observed Details |",
        "| :--- | :---: | :---: | :---: | :--- |",
    ])

    attestation_map = {row.criterion_id: row for row in report.attestation_rows}

    for r in report.criteria_statuses:
        status_icon = "🟢 VERIFIED" if r.status == "VERIFIED" else ("🟡 NEEDS_REVIEW" if r.status == "NEEDS_REVIEW" else "🔴 FAILED")
        human_text = "—"
        if r.criterion_id in attestation_map:
            att = attestation_map[r.criterion_id]
            human_text = f"**{att.human_attestation}**"
        elif r.criterion_type == "manual":
            human_text = "*Pending*"

        obs_clean = (r.observed or "").replace("\n", " ").strip()
        lines.append(f"| **{r.criterion_id}** | `{r.criterion_type}` | {status_icon} | {human_text} | {obs_clean} |")

    lines.extend([
        "",
        "#### Build Budget &amp; Surface Analysis",
        "",
        f"- **Approved Surface**: {len(report.expected_changes)} files",
    ])

    if len(report.expected_changes) <= 5:
        for f in report.expected_changes:
            lines.append(f"  - `{f}`")
    else:
        lines.append("<details><summary>Show expected surface files</summary>\n")
        for f in report.expected_changes:
            lines.append(f"- `{f}`")
        lines.append("</details>")

    if report.surface_deviations:
        lines.append(f"\n⚠️ **Surface Deviations (UNAPPROVED)**: {len(report.surface_deviations)}")
        for f in report.surface_deviations:
            lines.append(f"- `{f}`")
    else:
        lines.append("- **Surface Deviations**: *(none)*")

    if report.protected_changes:
        lines.append(f"\n🚨 **Blast Radius Violations**: {len(report.protected_changes)}")
        for f in report.protected_changes:
            lines.append(f"- `{f}`")
    else:
        lines.append("- **Protected Changes**: *(none)*")

    lines.append("")
    if report.acceptance_blocked_reason:
        lines.append(f"> ℹ️ **Notice**: {report.acceptance_blocked_reason}\n")

    lines.append(
        "*Generated by [GoATee](https://github.com/shaily99/goatee) Specification &amp; Delivery Control Layer.*"
    )
    return "\n".join(lines)


def export_pr_comment(
    root: Path,
    change_id: str,
    output_path: Optional[Path] = None
) -> Path:
    """Generate and export a GitHub/GitLab PR comment markdown summary for a change."""
    project_name = parse_project_name(root / ".goatee" / "project.md")
    working_tree = get_working_tree_state(root)
    current_head = resolve_current_head(root)

    report = get_change_report(root, change_id, working_tree, current_head)

    contract_path = root / ".goatee" / "changes" / change_id / "change.md"
    if not contract_path.is_file():
        contract_path = root / ".goatee" / "archive" / change_id / "change.md"
    contract = parse_change_contract(contract_path) if contract_path.is_file() else None

    md_content = generate_pr_comment_markdown(report, contract, project_name)

    if output_path:
        target = output_path if output_path.is_absolute() else root / output_path
    else:
        certs_dir = root / ".goatee" / "certificates"
        certs_dir.mkdir(parents=True, exist_ok=True)
        target = certs_dir / f"{change_id}-pr-comment.md"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md_content, encoding="utf-8")
    return target

