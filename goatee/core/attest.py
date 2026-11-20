"""Human attestation engine and review artifact lifecycle management for GoATee."""

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from goatee.core.models import AcceptanceCriterion, ChangeContract
from goatee.core.parser import parse_change_contract, ContractSyntaxError
from goatee.core.status import (
    ControlStateError,
    AttestationRow,
    ReceiptRow,
    ReceiptSnapshot,
    ReceiptSyntaxError,
    find_project_root,
    parse_receipt,
    validate_contract_receipt_consistency,
)


VALID_REVIEW_VERDICTS: Set[str] = {"PENDING", "ACCEPTED", "REJECTED"}
VALID_HUMAN_ATTESTATIONS: Set[str] = {"VERIFIED", "REJECTED", "PENDING"}


@dataclass(frozen=True)
class ReviewEvent:
    sequence: int
    attested_at: str
    scope: str  # "criterion" or "change"
    criterion_id: Optional[str]
    observation: str  # "VERIFIED" or "REJECTED"
    reviewer_name: str
    reviewer_email: str
    attribution: str
    note: str


@dataclass(frozen=True)
class ReviewRecord:
    change_id: str
    verdict: str
    updated_at: str
    verified_commit: str
    receipt_commit: str
    receipt_sha256: str
    rows: List[AttestationRow]
    events: List[ReviewEvent]


def normalize_note(note: str) -> str:
    """Normalize and validate --note argument, failing closed if invalid."""
    if not isinstance(note, str):
        raise ControlStateError("--note must be a string.")
    trimmed = note.strip()
    if not trimmed:
        raise ControlStateError("--note must not be empty.")
    if "\r" in trimmed or "\n" in trimmed:
        raise ControlStateError("--note must be a single line without line breaks.")
    return trimmed


def escape_table_cell(text: str) -> str:
    """Escape backslashes and pipe characters for Markdown table cells."""
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace("|", "\\|")


def get_git_reviewer(repo_root: Path) -> Tuple[str, str]:
    """Retrieve user.name and user.email from local Git configuration."""
    name_res = subprocess.run(
        ["git", "config", "user.name"],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    email_res = subprocess.run(
        ["git", "config", "user.email"],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    name = name_res.stdout.strip()
    email = email_res.stdout.strip()
    if not name or not email:
        raise ControlStateError(
            "Git identity (user.name / user.email) not configured. "
            "Configure user.name and user.email before recording human attestation."
        )
    if "\r" in name or "\n" in name or "\r" in email or "\n" in email:
        raise ControlStateError(
            "Error: Git user.name and user.email must contain single-line values before attesting."
        )
    return name, email


def check_clean_worktree(repo_root: Path) -> None:
    """Ensure git status --porcelain is completely clean."""
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res.returncode != 0:
        raise ControlStateError(f"Git inspection failed: {res.stderr.strip()}")
    if res.stdout.strip():
        raise ControlStateError("Working tree must be clean before recording human attestation.")


def verify_receipt_prerequisites(
    change_dir: Path,
    contract: ChangeContract,
    repo_root: Path
) -> Tuple[ReceiptSnapshot, str, str]:
    """Verify that receipt.md exists, is tracked, committed, byte-identical to HEAD, and consistent."""
    receipt_path = change_dir / "receipt.md"
    if not receipt_path.is_file():
        raise ControlStateError(
            f"Change '{contract.change_id}' has no delivery receipt. "
            f"Run 'goatee verify {contract.change_id}' before attesting."
        )

    rel_receipt = receipt_path.relative_to(repo_root)

    # Must be tracked in Git
    res_track = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel_receipt)],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res_track.returncode != 0:
        raise ControlStateError(
            f"Delivery receipt for '{contract.change_id}' is untracked. "
            "Commit the receipt before recording human attestation."
        )

    # Must be byte-identical to HEAD (clean in git diff HEAD)
    res_diff = subprocess.run(
        ["git", "diff", "HEAD", "--", str(rel_receipt)],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res_diff.returncode != 0 or res_diff.stdout.strip():
        raise ControlStateError(
            f"Delivery receipt for '{contract.change_id}' has uncommitted changes. "
            "Commit the receipt before recording human attestation."
        )

    # Resolve most recent commit affecting tracked receipt path
    res_log = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", str(rel_receipt)],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    receipt_commit = res_log.stdout.strip()
    if not receipt_commit:
        raise ControlStateError(
            f"Could not resolve Git commit for tracked receipt '{rel_receipt}'."
        )

    receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    receipt = parse_receipt(receipt_path)
    validate_contract_receipt_consistency(change_dir, contract, receipt)

    return receipt, receipt_commit, receipt_sha256


def derive_review_state(
    contract: ChangeContract,
    receipt: ReceiptSnapshot,
    events: List[ReviewEvent]
) -> Tuple[str, List[AttestationRow]]:
    """Replay event history to compute current criteria states and overall Review-Verdict."""
    # Validate sequence IDs
    for idx, ev in enumerate(events):
        expected_seq = idx + 1
        if ev.sequence != expected_seq:
            raise ControlStateError(
                f"Invalid event sequence ID '{ev.sequence:04d}', expected '{expected_seq:04d}'."
            )

    manual_states: Dict[str, Tuple[str, str, str, str]] = {}  # ac_id -> (status, reviewer, timestamp, note)
    for ac in contract.acceptance_criteria:
        if ac.evidence_type == "manual":
            manual_states[ac.id] = ("PENDING", "", "", "")

    automated_rejections: Dict[str, Tuple[str, str, str, str]] = {}
    has_change_rejection = False

    receipt_row_map = {r.criterion_id: r for r in receipt.rows}

    # Validate type equality between contract and receipt
    for ac in contract.acceptance_criteria:
        rec_row = receipt_row_map.get(ac.id)
        if not rec_row:
            raise ControlStateError(f"Criterion '{ac.id}' missing from delivery receipt.")
        if ac.evidence_type != rec_row.criterion_type:
            raise ControlStateError(
                f"Error: Criterion '{ac.id}' type differs between change contract and delivery receipt. "
                "Review cannot continue against inconsistent evidence."
            )

    for ev in events:
        reviewer_str = f"{ev.reviewer_name} <{ev.reviewer_email}> ({ev.attribution} attribution)"
        if ev.scope == "change":
            if ev.observation == "REJECTED":
                has_change_rejection = True
        elif ev.scope == "criterion":
            c_id = ev.criterion_id
            if not c_id:
                raise ControlStateError("Criterion event missing criterion_id.")
            # Find criterion in contract
            ac_match = next((ac for ac in contract.acceptance_criteria if ac.id == c_id), None)
            if not ac_match:
                raise ControlStateError(f"Event references unknown criterion '{c_id}'.")

            if ac_match.evidence_type == "manual":
                if ev.observation in ("VERIFIED", "REJECTED"):
                    manual_states[c_id] = (ev.observation, reviewer_str, ev.attested_at, ev.note)
            elif ac_match.evidence_type == "automated":
                if ev.observation == "REJECTED":
                    automated_rejections[c_id] = ("REJECTED", reviewer_str, ev.attested_at, ev.note)
                else:
                    raise ControlStateError(
                        f"Event contains invalid positive attestation for automated criterion '{c_id}'."
                    )

    # Build matrix rows: ALL manual criteria + automated criteria with human REJECTED events
    rows: List[AttestationRow] = []

    for ac in contract.acceptance_criteria:
        rec_row = receipt_row_map.get(ac.id)
        if rec_row and rec_row.criterion_type == "manual":
            m_state, r_name, r_time, r_note = manual_states.get(ac.id, ("PENDING", "", "", ""))
            rows.append(AttestationRow(
                criterion_id=ac.id,
                criterion_type=rec_row.criterion_type,
                machine_status=rec_row.status,
                human_attestation=m_state,
                reviewer=r_name if r_name else None,
                attested_at=r_time if r_time else None,
                note=r_note if r_note else None,
            ))

    # Add automated rejections in contract order
    for ac in contract.acceptance_criteria:
        rec_row = receipt_row_map.get(ac.id)
        if rec_row and rec_row.criterion_type == "automated" and ac.id in automated_rejections:
            m_state, r_name, r_time, r_note = automated_rejections[ac.id]
            rows.append(AttestationRow(
                criterion_id=ac.id,
                criterion_type=rec_row.criterion_type,
                machine_status=rec_row.status,
                human_attestation="REJECTED",
                reviewer=r_name,
                attested_at=r_time,
                note=r_note,
            ))

    # Derive overall Review-Verdict
    # 1. Any human rejection -> REJECTED
    any_manual_rejected = any(st == "REJECTED" for st, _, _, _ in manual_states.values())
    if any_manual_rejected or bool(automated_rejections) or has_change_rejection:
        verdict = "REJECTED"
    else:
        # 2. Check acceptance conditions:
        # - all automated ACs VERIFIED
        # - no automated FAILED or NOT_RUN
        # - zero Surface Deviations
        # - zero Protected Changes
        # - zero Verification Mutations
        # - every manual criterion current state VERIFIED
        clean_machine = (
            all(r.status == "VERIFIED" for r in receipt.rows if r.criterion_type == "automated")
            and not any(r.status in ("FAILED", "NOT_RUN") for r in receipt.rows if r.criterion_type == "automated")
            and len(receipt.surface_deviations) == 0
            and len(receipt.protected_changes) == 0
            and len(receipt.mutations) == 0
        )
        all_manual_verified = (
            bool(manual_states) and all(st == "VERIFIED" for st, _, _, _ in manual_states.values())
        )
        if clean_machine and all_manual_verified:
            verdict = "ACCEPTED"
        else:
            verdict = "PENDING"

    return verdict, rows


def format_review_markdown(
    change_id: str,
    verdict: str,
    updated_at: str,
    verified_commit: str,
    receipt_commit: str,
    receipt_sha256: str,
    rows: List[AttestationRow],
    events: List[ReviewEvent]
) -> str:
    """Format review.md strictly using deterministic canonical schema."""
    lines = [
        f"# Human Review: {change_id}",
        "",
        f"Change-ID: {change_id}",
        f"Review-Verdict: {verdict}",
        f"Updated-At: {updated_at}",
        "",
        "## Receipt Binding",
        "",
        f"Verified-Commit: {verified_commit}",
        f"Receipt-Commit: {receipt_commit}",
        f"Receipt-SHA256: {receipt_sha256}",
        "",
        "## Attestation Coverage Matrix",
        "",
        "| Criterion | Type | Machine Status | Human Attestation | Reviewer | Attested At | Note |",
        "| :--- | :---: | :---: | :---: | :--- | :--- | :--- |"
    ]

    for r in rows:
        reviewer_esc = escape_table_cell(r.reviewer or "")
        note_esc = escape_table_cell(r.note or "")
        time_str = r.attested_at or ""
        lines.append(
            f"| **{r.criterion_id}** | {r.criterion_type} | {r.machine_status} | "
            f"**{r.human_attestation}** | {reviewer_esc} | {time_str} | {note_esc} |"
        )

    lines.append("")
    lines.append("## Review Event History")
    lines.append("")

    for ev in events:
        lines.append(f"- Event: {ev.sequence:04d}")
        lines.append(f"  Attested-At: {ev.attested_at}")
        lines.append(f"  Scope: {ev.scope}")
        if ev.scope == "criterion":
            lines.append(f"  Criterion: {ev.criterion_id}")
        lines.append(f"  Observation: {ev.observation}")
        lines.append(f"  Reviewer-Name: {ev.reviewer_name}")
        lines.append(f"  Reviewer-Email: {ev.reviewer_email}")
        lines.append(f"  Attribution: {ev.attribution}")
        lines.append(f"  Note: {ev.note}")

    return "\n".join(lines) + "\n"


def parse_review_markdown(review_path: Path) -> ReviewRecord:
    """Parse review.md into ReviewRecord, validating canonical syntax and sequence IDs."""
    if not review_path.is_file():
        raise FileNotFoundError(f"Review file not found: {review_path}")

    content = review_path.read_text(encoding="utf-8")

    # Header and Change-ID
    header_m = re.search(r"^# Human Review:\s*(.+)$", content, re.MULTILINE)
    if not header_m:
        raise ControlStateError(f"Malformed review in '{review_path}': missing '# Human Review: <id>' header.")
    header_change_id = header_m.group(1).strip()

    id_m = re.search(r"^Change-ID:\s*(.+)$", content, re.MULTILINE)
    if not id_m:
        raise ControlStateError(f"Malformed review in '{review_path}': missing 'Change-ID:' field.")
    change_id = id_m.group(1).strip()

    if header_change_id != change_id:
        raise ControlStateError(
            f"Malformed review in '{review_path}': header change ID '{header_change_id}' != Change-ID '{change_id}'."
        )

    # Verdict
    verd_m = re.search(r"^Review-Verdict:\s*(.+)$", content, re.MULTILINE)
    if not verd_m:
        raise ControlStateError(f"Malformed review in '{review_path}': missing 'Review-Verdict:' field.")
    verdict = verd_m.group(1).strip()
    if verdict not in VALID_REVIEW_VERDICTS:
        raise ControlStateError(f"Malformed review in '{review_path}': unrecognized Review-Verdict '{verdict}'.")

    # Updated-At
    up_m = re.search(r"^Updated-At:\s*(.+)$", content, re.MULTILINE)
    if not up_m:
        raise ControlStateError(f"Malformed review in '{review_path}': missing 'Updated-At:' field.")
    updated_at = up_m.group(1).strip()

    # Receipt Binding
    if "## Receipt Binding" not in content:
        raise ControlStateError(f"Malformed review in '{review_path}': missing '## Receipt Binding' section.")
    ver_m = re.search(r"^Verified-Commit:\s*(.+)$", content, re.MULTILINE)
    rec_c_m = re.search(r"^Receipt-Commit:\s*(.+)$", content, re.MULTILINE)
    rec_s_m = re.search(r"^Receipt-SHA256:\s*(.+)$", content, re.MULTILINE)

    if not ver_m or not rec_c_m or not rec_s_m:
        raise ControlStateError(f"Malformed review in '{review_path}': incomplete Receipt Binding fields.")

    verified_commit = ver_m.group(1).strip()
    receipt_commit = rec_c_m.group(1).strip()
    receipt_sha256 = rec_s_m.group(1).strip()

    # Attestation Coverage Matrix
    if "## Attestation Coverage Matrix" not in content:
        raise ControlStateError(f"Malformed review in '{review_path}': missing '## Attestation Coverage Matrix' section.")

    rows: List[AttestationRow] = []
    matrix_parts = re.split(r"\n##\s+", content.split("## Attestation Coverage Matrix")[1], maxsplit=1)
    matrix_section = matrix_parts[0] if matrix_parts else ""

    for line in matrix_section.splitlines():
        line = line.strip()
        if not line.startswith("|") or ":---" in line or "Criterion" in line:
            continue
        parts = [p.replace(r"\|", "|").strip() for p in re.split(r"(?<!\\)\|", line)]
        # "| **AC-11** | manual | NEEDS_REVIEW | **VERIFIED** | Rev | Time | Note |" -> 9 parts
        if len(parts) >= 8:
            c_id = parts[1].replace("*", "").strip()
            c_type = parts[2].strip()
            m_stat = parts[3].strip()
            h_stat = parts[4].replace("*", "").strip()
            rev = parts[5].strip() or None
            at_time = parts[6].strip() or None
            note_val = parts[7].strip() or None
            rows.append(AttestationRow(
                criterion_id=c_id,
                criterion_type=c_type,
                machine_status=m_stat,
                human_attestation=h_stat,
                reviewer=rev,
                attested_at=at_time,
                note=note_val,
            ))

    # Review Event History
    if "## Review Event History" not in content:
        raise ControlStateError(f"Malformed review in '{review_path}': missing '## Review Event History' section.")

    events_section = content.split("## Review Event History")[1]
    event_blocks = re.split(r"\n(?=- Event:\s*\d+)", events_section)

    events: List[ReviewEvent] = []
    for block in event_blocks:
        block = block.strip()
        if not block or not block.startswith("- Event:"):
            continue
        seq_m = re.search(r"^- Event:\s*(\d+)$", block, re.MULTILINE)
        if not seq_m:
            raise ControlStateError(f"Malformed event block in '{review_path}': invalid Event sequence header.")
        seq_num = int(seq_m.group(1))

        time_m = re.search(r"^  Attested-At:\s*(.+)$", block, re.MULTILINE)
        scope_m = re.search(r"^  Scope:\s*(.+)$", block, re.MULTILINE)
        obs_m = re.search(r"^  Observation:\s*(.+)$", block, re.MULTILINE)
        name_m = re.search(r"^  Reviewer-Name:\s*(.+)$", block, re.MULTILINE)
        email_m = re.search(r"^  Reviewer-Email:\s*(.+)$", block, re.MULTILINE)
        attr_m = re.search(r"^  Attribution:\s*(.+)$", block, re.MULTILINE)
        note_m = re.search(r"^  Note:\s*(.+)$", block, re.MULTILINE)

        if not (time_m and scope_m and obs_m and name_m and email_m and attr_m and note_m):
            raise ControlStateError(f"Malformed event {seq_num:04d} in '{review_path}': missing required fields.")

        scope_val = scope_m.group(1).strip()
        crit_id = None
        if scope_val == "criterion":
            crit_m = re.search(r"^  Criterion:\s*(.+)$", block, re.MULTILINE)
            if not crit_m:
                raise ControlStateError(f"Malformed event {seq_num:04d} in '{review_path}': missing Criterion field.")
            crit_id = crit_m.group(1).strip()

        events.append(ReviewEvent(
            sequence=seq_num,
            attested_at=time_m.group(1).strip(),
            scope=scope_val,
            criterion_id=crit_id,
            observation=obs_m.group(1).strip(),
            reviewer_name=name_m.group(1).strip(),
            reviewer_email=email_m.group(1).strip(),
            attribution=attr_m.group(1).strip(),
            note=note_m.group(1).strip(),
        ))

    # Validate sequence numbers strictly increasing and consecutive
    for idx, ev in enumerate(events):
        expected_seq = idx + 1
        if ev.sequence != expected_seq:
            raise ControlStateError(
                f"Invalid event sequence ID in '{review_path}': found {ev.sequence:04d}, expected {expected_seq:04d}."
            )

    return ReviewRecord(
        change_id=change_id,
        verdict=verdict,
        updated_at=updated_at,
        verified_commit=verified_commit,
        receipt_commit=receipt_commit,
        receipt_sha256=receipt_sha256,
        rows=rows,
        events=events,
    )


def load_and_validate_review(
    change_dir: Path,
    contract: ChangeContract,
    receipt: ReceiptSnapshot,
    repo_root: Path
) -> Optional[ReviewRecord]:
    """Load review.md if present, strictly verifying commit state, binding, and event projections."""
    review_path = change_dir / "review.md"
    if not review_path.is_file():
        return None

    rel_path = review_path.relative_to(repo_root)

    # Check Git tracking & cleanliness: review.md must be committed and clean
    res_track = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel_path)],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res_track.returncode != 0:
        raise ControlStateError(
            f"Review record for '{contract.change_id}' has uncommitted changes. "
            f"Commit {rel_path} before checking status."
        )

    res_diff = subprocess.run(
        ["git", "diff", "HEAD", "--", str(rel_path)],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    if res_diff.returncode != 0 or res_diff.stdout.strip():
        raise ControlStateError(
            f"Review record for '{contract.change_id}' has uncommitted changes. "
            f"Commit {rel_path} before checking status."
        )

    review = parse_review_markdown(review_path)

    # Identity check
    if review.change_id != contract.change_id:
        raise ControlStateError(
            f"Inconsistent control state for '{contract.change_id}': "
            f"review change ID '{review.change_id}' does not match contract '{contract.change_id}'."
        )

    # Stale review / Binding check
    receipt_path = change_dir / "receipt.md"
    current_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    res_log = subprocess.run(
        ["git", "log", "--follow", "--format=%H", "--", str(receipt_path.relative_to(repo_root))],
        capture_output=True,
        text=True,
        cwd=repo_root
    )
    receipt_commits = [c.strip() for c in res_log.stdout.splitlines() if c.strip()]
    current_receipt_commit = receipt_commits[0] if receipt_commits else ""

    is_archived = (change_dir.parent.name == "archive")
    commit_matches = (
        (review.receipt_commit == current_receipt_commit)
        or (is_archived and review.receipt_commit in receipt_commits)
    )

    if (
        review.receipt_sha256 != current_sha256
        or not commit_matches
        or review.verified_commit != receipt.verified_commit
    ):
        raise ControlStateError(
            f"Review record for '{contract.change_id}' is stale "
            f"(bound to receipt commit {review.receipt_commit[:7]} (hash {review.receipt_sha256[:8]}), "
            f"current is {current_receipt_commit[:7]} (hash {current_sha256[:8]}))."
        )

    # Replay event history and reconcile projections
    derived_verdict, derived_rows = derive_review_state(contract, receipt, review.events)
    if review.verdict != derived_verdict:
        raise ControlStateError(
            f"Review artifact projection mismatch in '{review_path}': "
            f"stored Review-Verdict '{review.verdict}' does not match event history '{derived_verdict}'."
        )

    # Check rows match
    if len(review.rows) != len(derived_rows):
        raise ControlStateError(
            f"Review artifact projection mismatch in '{review_path}': "
            f"stored Coverage Matrix rows ({len(review.rows)}) do not match event history rows ({len(derived_rows)})."
        )

    for s_row, d_row in zip(review.rows, derived_rows):
        if (
            s_row.criterion_id != d_row.criterion_id
            or s_row.criterion_type != d_row.criterion_type
            or s_row.machine_status != d_row.machine_status
            or s_row.human_attestation != d_row.human_attestation
        ):
            raise ControlStateError(
                f"Review artifact projection mismatch in '{review_path}': "
                f"row for '{s_row.criterion_id}' does not match event history projection."
            )

    return review


def record_attestation(
    repo_root: Path,
    change_id: str,
    criterion_id: Optional[str],
    verified: bool,
    rejected: bool,
    note: str
) -> None:
    """Execute goatee attest operation, enforcing all I-5 governance rules and mutation boundaries."""
    root = find_project_root(repo_root)
    check_clean_worktree(root)

    change_dir = root / ".goatee" / "changes" / change_id
    change_md_path = change_dir / "change.md"
    if not change_md_path.is_file():
        raise ControlStateError(f"Change '{change_id}' not found.")

    try:
        contract = parse_change_contract(change_md_path)
    except ContractSyntaxError as e:
        raise ControlStateError(f"Malformed contract in '{change_md_path}': {e}")

    receipt, receipt_commit, receipt_sha256 = verify_receipt_prerequisites(change_dir, contract, root)

    # Normalize note
    clean_note = normalize_note(note)

    # Resolve reviewer
    reviewer_name, reviewer_email = get_git_reviewer(root)

    # Check eligibility
    rec_row_map = {r.criterion_id: r for r in receipt.rows}
    if criterion_id:
        ac_match = next((ac for ac in contract.acceptance_criteria if ac.id == criterion_id), None)
        if not ac_match:
            raise ControlStateError(f"Criterion '{criterion_id}' not found in change contract '{change_id}'.")
        rec_row = rec_row_map.get(criterion_id)
        if not rec_row:
            raise ControlStateError(f"Criterion '{criterion_id}' missing from delivery receipt.")
        if ac_match.evidence_type != rec_row.criterion_type:
            raise ControlStateError(
                f"Error: Criterion '{criterion_id}' type differs between change contract and delivery receipt. "
                "Review cannot continue against inconsistent evidence."
            )

    if verified:
        if not criterion_id:
            raise ControlStateError("--verified requires --criterion.")
        if rec_row.criterion_type != "manual" or ac_match.evidence_type != "manual":
            raise ControlStateError(
                f"Criterion '{criterion_id}' is declared as automated in change contract. "
                "Automated criteria must be verified mechanically by 'goatee verify'. "
                "Human attestation with --verified is only permitted for 'type: manual' criteria."
            )
        if rec_row.status != "NEEDS_REVIEW":
            raise ControlStateError(
                f"Criterion '{criterion_id}' cannot be positively attested because its machine status is {rec_row.status}. "
                "Human attestation may only resolve manual criteria with machine status NEEDS_REVIEW."
            )
        clean_declared = rec_row.declared.strip()
        if clean_declared.startswith("`") and clean_declared.endswith("`") and len(clean_declared) >= 2:
            unquoted_declared = clean_declared[1:-1].strip()
        else:
            unquoted_declared = clean_declared

        contract_instruction = (ac_match.instruction or "").strip()
        if contract_instruction != clean_declared and contract_instruction != unquoted_declared:
            raise ControlStateError(
                f"Error: Criterion '{criterion_id}' manual evidence instruction differs from the verified receipt. "
                "Human attestation cannot be applied to changed evidence requirements."
            )
    elif rejected:
        if criterion_id:
            pass
    else:
        raise ControlStateError("Exactly one of --verified or --rejected must be specified.")

    # Existing review check
    review_path = change_dir / "review.md"
    existing_events: List[ReviewEvent] = []
    if review_path.is_file():
        rel_review = review_path.relative_to(root)
        # Check tracked and committed in HEAD
        res_track = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel_review)],
            capture_output=True,
            text=True,
            cwd=root
        )
        if res_track.returncode != 0:
            raise ControlStateError(
                f"Review record for '{change_id}' has uncommitted changes. "
                f"Commit {rel_review} before recording another attestation."
            )
        res_diff = subprocess.run(
            ["git", "diff", "HEAD", "--", str(rel_review)],
            capture_output=True,
            text=True,
            cwd=root
        )
        if res_diff.returncode != 0 or res_diff.stdout.strip():
            raise ControlStateError(
                f"Review record for '{change_id}' has uncommitted changes. "
                f"Commit {rel_review} before recording another attestation."
            )

        existing_review = load_and_validate_review(change_dir, contract, receipt, root)
        if existing_review:
            existing_events = list(existing_review.events)

    next_seq = len(existing_events) + 1
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    new_event = ReviewEvent(
        sequence=next_seq,
        attested_at=now_utc,
        scope="criterion" if criterion_id else "change",
        criterion_id=criterion_id,
        observation="VERIFIED" if verified else "REJECTED",
        reviewer_name=reviewer_name,
        reviewer_email=reviewer_email,
        attribution="Git-configured",
        note=clean_note,
    )

    all_events = existing_events + [new_event]
    verdict, rows = derive_review_state(contract, receipt, all_events)

    review_text = format_review_markdown(
        change_id=change_id,
        verdict=verdict,
        updated_at=now_utc,
        verified_commit=receipt.verified_commit,
        receipt_commit=receipt_commit,
        receipt_sha256=receipt_sha256,
        rows=rows,
        events=all_events,
    )

    import os
    tmp_path = review_path.with_suffix(".tmp")
    tmp_path.write_text(review_text, encoding="utf-8")
    os.replace(tmp_path, review_path)


def run_interactive_attestation(
    repo_root: Path,
    change_id: str,
    input_fn=input,
    output_fn=print,
) -> int:
    """Run an interactive wizard to record human attestation for pending criteria."""
    root = find_project_root(repo_root)
    check_clean_worktree(root)

    change_dir = root / ".goatee" / "changes" / change_id
    change_md_path = change_dir / "change.md"
    if not change_md_path.is_file():
        raise ControlStateError(f"Change '{change_id}' not found.")

    try:
        contract = parse_change_contract(change_md_path)
    except ContractSyntaxError as e:
        raise ControlStateError(f"Malformed contract in '{change_md_path}': {e}")

    receipt, receipt_commit, receipt_sha256 = verify_receipt_prerequisites(change_dir, contract, root)
    reviewer_name, reviewer_email = get_git_reviewer(root)

    # Load existing review events if review.md exists
    review_path = change_dir / "review.md"
    existing_events: List[ReviewEvent] = []
    if review_path.is_file():
        existing_review = load_and_validate_review(change_dir, contract, receipt, root)
        if existing_review:
            existing_events = list(existing_review.events)

    all_events = list(existing_events)
    derived_verdict, current_rows = derive_review_state(contract, receipt, all_events)
    row_map = {r.criterion_id: r for r in current_rows}
    rec_row_map = {r.criterion_id: r for r in receipt.rows}

    # Find criteria available for attestation:
    # 1. Manual criteria where human_attestation != 'VERIFIED'
    # 2. Automated criteria where machine_status != 'VERIFIED' and human_attestation != 'REJECTED'
    pending_criteria = []
    for ac in contract.acceptance_criteria:
        row = row_map.get(ac.id)
        current_human = row.human_attestation if row else "PENDING"
        rec_row = rec_row_map.get(ac.id)
        rec_status = rec_row.status if rec_row else "NEEDS_REVIEW"

        if ac.evidence_type == "manual":
            if current_human != "VERIFIED":
                pending_criteria.append((ac, rec_status, current_human))
        else:
            if rec_status != "VERIFIED" and current_human != "REJECTED":
                pending_criteria.append((ac, rec_status, current_human))

    if not pending_criteria:
        output_fn(f"All eligible criteria for change '{change_id}' are already attested.")
        output_fn(f"Current Human Review Verdict: {derived_verdict}")
        return 0

    output_fn(f"\n=== Interactive Attestation: {change_id} ===")
    output_fn(f"Reviewer: {reviewer_name} <{reviewer_email}> (Git-configured)")
    output_fn(f"Found {len(pending_criteria)} criterion/criteria pending review.\n")

    new_events_count = 0

    for idx, (ac, machine_stat, human_stat) in enumerate(pending_criteria, 1):
        output_fn(f"[{idx}/{len(pending_criteria)}] Criterion {ac.id} ({ac.evidence_type})")
        output_fn(f"  Description: {ac.description}")
        if ac.evidence_type == "manual":
            output_fn(f"  Instruction: {ac.instruction}")
        else:
            output_fn(f"  Command: {ac.command} ({ac.expected})")
        output_fn(f"  Machine Status: {machine_stat}")
        output_fn(f"  Current Human State: {human_stat}")

        choice = ""
        while True:
            if ac.evidence_type == "manual":
                choice = input_fn("  Action ([v]erify / [r]eject / [s]kip / [q]uit): ").strip().lower()
            else:
                choice = input_fn("  Action ([r]eject / [s]kip / [q]uit): ").strip().lower()

            if choice in ("q", "quit"):
                output_fn("\nExiting interactive attestation.")
                break
            elif choice in ("s", "skip", ""):
                output_fn("  -> Skipped.")
                break
            elif choice in ("v", "verify"):
                if ac.evidence_type != "manual":
                    output_fn("  Error: Automated criteria cannot be positively attested with 'verify'. Use 'reject' or 'skip'.")
                    continue
                note = input_fn("  Rationale note (single line): ").strip()
                try:
                    clean_note = normalize_note(note)
                except ControlStateError as cse:
                    output_fn(f"  Error: {cse}")
                    continue

                now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                ev = ReviewEvent(
                    sequence=len(all_events) + 1,
                    attested_at=now_utc,
                    scope="criterion",
                    criterion_id=ac.id,
                    observation="VERIFIED",
                    reviewer_name=reviewer_name,
                    reviewer_email=reviewer_email,
                    attribution="Git-configured",
                    note=clean_note,
                )
                all_events.append(ev)
                new_events_count += 1
                output_fn(f"  -> Recorded VERIFIED for {ac.id}.")
                break
            elif choice in ("r", "reject"):
                note = input_fn("  Rejection rationale note (single line): ").strip()
                try:
                    clean_note = normalize_note(note)
                except ControlStateError as cse:
                    output_fn(f"  Error: {cse}")
                    continue

                now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                ev = ReviewEvent(
                    sequence=len(all_events) + 1,
                    attested_at=now_utc,
                    scope="criterion",
                    criterion_id=ac.id,
                    observation="REJECTED",
                    reviewer_name=reviewer_name,
                    reviewer_email=reviewer_email,
                    attribution="Git-configured",
                    note=clean_note,
                )
                all_events.append(ev)
                new_events_count += 1
                output_fn(f"  -> Recorded REJECTED for {ac.id}.")
                break
            else:
                output_fn(f"  Invalid choice '{choice}'.")

        if choice in ("q", "quit"):
            break

    if new_events_count > 0:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        verdict, rows = derive_review_state(contract, receipt, all_events)
        review_text = format_review_markdown(
            change_id=change_id,
            verdict=verdict,
            updated_at=now_utc,
            verified_commit=receipt.verified_commit,
            receipt_commit=receipt_commit,
            receipt_sha256=receipt_sha256,
            rows=rows,
            events=all_events,
        )
        import os
        tmp_path = review_path.with_suffix(".tmp")
        tmp_path.write_text(review_text, encoding="utf-8")
        os.replace(tmp_path, review_path)
        output_fn(f"\nRecorded {new_events_count} new attestation event(s) in {review_path}.")
        output_fn(f"Updated Human Review Verdict: {verdict}")
        output_fn("Notice: Commit review.md to preserve these review records in Git history.")
    else:
        output_fn("\nNo new attestation events recorded.")

    return 0


def parse_decision_argument(decision_str: str) -> Tuple[str, str, str]:
    """Parse single --decision string in format <criterion_id>=<VERIFIED|REJECTED>:<note>.
    
    Raises ControlStateError if syntax or format is invalid.
    """
    if not isinstance(decision_str, str):
        raise ControlStateError("--decision argument must be a string.")
    
    match = re.match(r"^([a-zA-Z0-9_-]+)=(VERIFIED|REJECTED):(.*)$", decision_str.strip())
    if not match:
        raise ControlStateError(
            f"Invalid --decision format '{decision_str}'. "
            "Expected format: '<criterion_id>=<VERIFIED|REJECTED>:<note>'."
        )
    
    criterion_id = match.group(1).strip()
    observation = match.group(2).strip()
    raw_note = match.group(3).strip()
    
    # Strip optional surrounding quotes from note if present
    if (raw_note.startswith('"') and raw_note.endswith('"')) or (raw_note.startswith("'") and raw_note.endswith("'")):
        raw_note = raw_note[1:-1]
        
    clean_note = normalize_note(raw_note)
    return criterion_id, observation, clean_note


def record_multi_attestation(
    repo_root: Path,
    change_id: str,
    decisions: Sequence[Union[str, Tuple[str, str, str]]]
) -> int:
    """Record an explicit batch of human criterion decisions with upfront validation and atomic replacement.
    
    Enforces that:
    1. Working tree outside .goatee/ is clean and receipt.md is committed and clean in HEAD.
    2. Batch contains no duplicate criterion decisions.
    3. All decision formats and single-line notes are valid.
    4. Automated criteria reject positive VERIFIED attestation (only REJECTED permitted).
    5. All criteria exist in contract and match receipt declared instructions.
    6. Replays all events in memory to verify state before writing.
    7. Atomically replaces review.md via temporary file write with zero partial mutation.
    """
    if not decisions:
        raise ControlStateError("No decisions provided in attestation batch.")

    root = find_project_root(repo_root)
    check_clean_worktree(root)

    change_dir = root / ".goatee" / "changes" / change_id
    change_md_path = change_dir / "change.md"
    if not change_md_path.is_file():
        raise ControlStateError(f"Change '{change_id}' not found.")

    try:
        contract = parse_change_contract(change_md_path)
    except ContractSyntaxError as e:
        raise ControlStateError(f"Malformed contract in '{change_md_path}': {e}")

    receipt, receipt_commit, receipt_sha256 = verify_receipt_prerequisites(change_dir, contract, root)
    reviewer_name, reviewer_email = get_git_reviewer(root)

    # Check existing review record in Git
    review_path = change_dir / "review.md"
    existing_events: List[ReviewEvent] = []
    if review_path.is_file():
        rel_review = review_path.relative_to(root)
        res_track = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel_review)],
            capture_output=True,
            text=True,
            cwd=root
        )
        if res_track.returncode != 0:
            raise ControlStateError(
                f"Review record for '{change_id}' has uncommitted changes. "
                f"Commit {rel_review} before recording another attestation."
            )
        res_diff = subprocess.run(
            ["git", "diff", "HEAD", "--", str(rel_review)],
            capture_output=True,
            text=True,
            cwd=root
        )
        if res_diff.returncode != 0 or res_diff.stdout.strip():
            raise ControlStateError(
                f"Review record for '{change_id}' has uncommitted changes. "
                f"Commit {rel_review} before recording another attestation."
            )

        existing_review = load_and_validate_review(change_dir, contract, receipt, root)
        if existing_review:
            existing_events = list(existing_review.events)

    # 1. Parse decision syntax and detect duplicate criterion entries upfront
    parsed_decisions: List[Tuple[str, str, str]] = []
    seen_criteria: Set[str] = set()

    for dec_item in decisions:
        if isinstance(dec_item, str):
            c_id, obs, clean_note = parse_decision_argument(dec_item)
        elif isinstance(dec_item, (tuple, list)) and len(dec_item) == 3:
            raw_cid, raw_obs, raw_note = dec_item
            c_id = str(raw_cid).strip()
            obs = str(raw_obs).strip().upper()
            if obs not in ("VERIFIED", "REJECTED"):
                raise ControlStateError(f"Invalid decision verdict '{obs}'. Must be VERIFIED or REJECTED.")
            clean_note = normalize_note(str(raw_note))
        else:
            raise ControlStateError(f"Invalid decision item '{dec_item}'. Expected string or (criterion_id, verdict, note) tuple.")

        if c_id in seen_criteria:
            raise ControlStateError(
                f"Duplicate decision for criterion '{c_id}' in decision batch. "
                "Each criterion may appear at most once per batch."
            )
        seen_criteria.add(c_id)
        parsed_decisions.append((c_id, obs, clean_note))

    # 2. Semantic validation against contract and receipt
    rec_row_map = {r.criterion_id: r for r in receipt.rows}

    for c_id, obs, clean_note in parsed_decisions:
        ac_match = next((ac for ac in contract.acceptance_criteria if ac.id == c_id), None)
        if not ac_match:
            raise ControlStateError(f"Criterion '{c_id}' not found in change contract '{change_id}'.")

        rec_row = rec_row_map.get(c_id)
        if not rec_row:
            raise ControlStateError(f"Criterion '{c_id}' missing from delivery receipt.")

        if ac_match.evidence_type != rec_row.criterion_type:
            raise ControlStateError(
                f"Error: Criterion '{c_id}' type differs between change contract and delivery receipt. "
                "Review cannot continue against inconsistent evidence."
            )

        if obs == "VERIFIED":
            if rec_row.criterion_type != "manual" or ac_match.evidence_type != "manual":
                raise ControlStateError(
                    f"Criterion '{c_id}' is declared as automated in change contract. "
                    "Automated criteria must be verified mechanically by 'goatee verify'. "
                    "Human attestation with VERIFIED is only permitted for 'type: manual' criteria."
                )
            if rec_row.status != "NEEDS_REVIEW":
                raise ControlStateError(
                    f"Criterion '{c_id}' cannot be positively attested because its machine status is {rec_row.status}. "
                    "Human attestation may only resolve manual criteria with machine status NEEDS_REVIEW."
                )
            clean_declared = rec_row.declared.strip()
            if clean_declared.startswith("`") and clean_declared.endswith("`") and len(clean_declared) >= 2:
                unquoted_declared = clean_declared[1:-1].strip()
            else:
                unquoted_declared = clean_declared

            contract_instruction = (ac_match.instruction or "").strip()
            if contract_instruction != clean_declared and contract_instruction != unquoted_declared:
                raise ControlStateError(
                    f"Error: Criterion '{c_id}' manual evidence instruction differs from the verified receipt. "
                    "Human attestation cannot be applied to changed evidence requirements."
                )
        elif obs == "REJECTED":
            # Rejection permitted for both manual and automated criteria
            pass
        else:
            raise ControlStateError(f"Unrecognized observation status '{obs}' for criterion '{c_id}'. Must be VERIFIED or REJECTED.")

    # 3. All decisions valid: construct review events sequentially
    all_events = list(existing_events)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    for c_id, obs, clean_note in parsed_decisions:
        new_event = ReviewEvent(
            sequence=len(all_events) + 1,
            attested_at=now_utc,
            scope="criterion",
            criterion_id=c_id,
            observation=obs,
            reviewer_name=reviewer_name,
            reviewer_email=reviewer_email,
            attribution="Git-configured",
            note=clean_note,
        )
        all_events.append(new_event)

    verdict, rows = derive_review_state(contract, receipt, all_events)

    review_text = format_review_markdown(
        change_id=change_id,
        verdict=verdict,
        updated_at=now_utc,
        verified_commit=receipt.verified_commit,
        receipt_commit=receipt_commit,
        receipt_sha256=receipt_sha256,
        rows=rows,
        events=all_events,
    )

    # 4. Atomic file replacement
    import os
    tmp_path = review_path.with_suffix(".tmp")
    tmp_path.write_text(review_text, encoding="utf-8")
    os.replace(tmp_path, review_path)

    return len(parsed_decisions)
