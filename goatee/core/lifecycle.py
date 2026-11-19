"""Change lifecycle management: landing accepted changes and archiving retired changes."""

import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from goatee.core.budget import is_control_artifact
from goatee.core.parser import (
    parse_change_contract,
    parse_metadata_block,
    update_contract_state,
    compute_contract_hash,
    read_contract_hash,
    ContractSyntaxError
)
from goatee.core.status import (
    parse_receipt,
    ControlStateError,
    ReceiptSyntaxError
)
from goatee.core.attest import (
    load_and_validate_review,
    validate_contract_receipt_consistency
)


class LifecycleError(Exception):
    """Raised when a lifecycle operation (land/archive) violates preconditions."""
    pass


def land_change(root: Path, change_id: str) -> Path:
    """Land an accepted change: verify acceptance preconditions, update lifecycle, and move to archive.
    
    Enforces that:
    1. Contract exists, parses, and has not been tampered with since approval.
    2. Delivery receipt exists, records no machine failures, no budget deviations, and no mutations.
    3. If manual acceptance criteria exist (or receipt is NEEDS_REVIEW), valid, untampered human review
       with Review-Verdict: ACCEPTED is required, binding to the exact receipt and verified commit.
    4. Implementation has not been modified post-verification.
    
    Returns destination path in .goatee/archive/<change_id>.
    """
    changes_dir = root / ".goatee" / "changes"
    change_dir = changes_dir / change_id
    contract_file = change_dir / "change.md"
    receipt_file = change_dir / "receipt.md"
    review_file = change_dir / "review.md"

    if not change_dir.is_dir() or not contract_file.is_file():
        raise LifecycleError(f"Change '{change_id}' not found at {change_dir}")

    # 1. Parse and validate contract state and approval
    try:
        contract = parse_change_contract(contract_file)
    except ContractSyntaxError as cse:
        raise LifecycleError(f"Change '{change_id}' contract syntax error: {cse}")

    # Require approved contract state
    if contract.contract_state.agreement_status != "APPROVED":
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: Agreement-Status is '{contract.contract_state.agreement_status}'. Must be 'APPROVED'."
        )
    if contract.contract_state.budget_status != "APPROVED":
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: Budget-Status is '{contract.contract_state.budget_status}'. Must be 'APPROVED'."
        )
    if not contract.contract_state.baseline_commit:
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: missing Baseline-Commit."
        )

    # Require present and valid contract hash
    locked_hash = read_contract_hash(contract_file)
    if not locked_hash:
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: contract has no locked Contract-SHA256."
        )
    current_hash = compute_contract_hash(contract_file)
    if current_hash != locked_hash:
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: contract integrity violation. "
            f"Approved hash {locked_hash[:8]} != current hash {current_hash[:8]}."
        )

    # 2. Require and validate receipt.md
    if not receipt_file.is_file():
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: delivery receipt missing. Run 'goatee verify {change_id}' first."
        )

    try:
        receipt = parse_receipt(receipt_file)
    except ReceiptSyntaxError as rse:
        raise LifecycleError(f"Change '{change_id}' receipt syntax error: {rse}")

    if receipt.verdict == "FAIL":
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: delivery receipt recorded FAIL."
        )

    # Validate contract-receipt consistency
    try:
        validate_contract_receipt_consistency(change_dir, contract, receipt)
    except ControlStateError as cse:
        raise LifecycleError(f"Change '{change_id}' cannot be landed: {cse}")

    # Check all automated criteria are VERIFIED and explicitly reject NOT_RUN / FAILED / REJECTED
    for row in receipt.rows:
        if row.status == "NOT_RUN":
            raise LifecycleError(
                f"Change '{change_id}' cannot be landed: criterion '{row.criterion_id}' was NOT_RUN."
            )
        if row.criterion_type == "automated" and row.status != "VERIFIED":
            raise LifecycleError(
                f"Change '{change_id}' cannot be landed: automated criterion '{row.criterion_id}' is '{row.status}'. Must be 'VERIFIED'."
            )

    if receipt.surface_deviations or receipt.protected_changes or receipt.mutations:
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: delivery receipt contains unapproved budget deviations or Git mutations."
        )

    # 3. Dual-verdict validation: check manual criteria and review.md with event replay
    has_manual_criteria = any(ac.evidence_type == "manual" for ac in contract.acceptance_criteria)
    if has_manual_criteria or receipt.verdict == "NEEDS_REVIEW":
        if not review_file.is_file():
            raise LifecycleError(
                f"Change '{change_id}' cannot be landed: human review missing for manual acceptance criteria. "
                f"Run 'goatee attest {change_id}' first."
            )

        try:
            review_record = load_and_validate_review(change_dir, contract, receipt, root)
        except ControlStateError as cse:
            raise LifecycleError(f"Change '{change_id}' review validation failed: {cse}")

        if not review_record or review_record.verdict != "ACCEPTED":
            raise LifecycleError(
                f"Change '{change_id}' cannot be landed: Review-Verdict is '{review_record.verdict if review_record else 'UNKNOWN'}'. Must be 'ACCEPTED'."
            )

        # Check all manual criteria in contract are verified by human
        attested_map = {row.criterion_id: row.human_attestation for row in review_record.rows}
        for ac in contract.acceptance_criteria:
            if ac.evidence_type == "manual":
                h_stat = attested_map.get(ac.id)
                if h_stat != "VERIFIED":
                    raise LifecycleError(
                        f"Change '{change_id}' cannot be landed: manual criterion '{ac.id}' is '{h_stat or 'UNATTESTED'}'. "
                        "All manual criteria must be VERIFIED in review.md."
                    )

    # 4. Invariant: Check that implementation was NOT modified post-verification
    # Fail closed on ANY Git inspection error
    res_status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        capture_output=True,
        text=True,
        cwd=root
    )
    if res_status.returncode != 0:
        raise LifecycleError(f"Git inspection error during landing (git status): {res_status.stderr.strip()}")

    uncommitted = [
        l for l in res_status.stdout.splitlines()
        if l.strip() and not is_control_artifact(l[3:].strip())
    ]
    if uncommitted:
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: uncommitted implementation modifications detected post-verification."
        )

    res_diff = subprocess.run(
        ["git", "diff", "--name-only", receipt.verified_commit, "HEAD"],
        capture_output=True,
        text=True,
        cwd=root
    )
    if res_diff.returncode != 0:
        raise LifecycleError(f"Git inspection error during landing (git diff): {res_diff.stderr.strip()}")

    app_diffs = [
        f.strip() for f in res_diff.stdout.splitlines()
        if f.strip() and not is_control_artifact(f.strip())
    ]
    if app_diffs:
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: implementation was modified after verification at commit "
            f"{receipt.verified_commit[:8]} (modified files: {', '.join(app_diffs[:3])})."
        )

    # 5. Update contract state with Lifecycle-Status and Landed-At
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    update_contract_state(contract_file, "Lifecycle-Status", "LANDED")
    update_contract_state(contract_file, "Landed-At", now_iso)

    # Update Contract-SHA256 after lifecycle state change
    new_hash = compute_contract_hash(contract_file)
    update_contract_state(contract_file, "Contract-SHA256", new_hash)

    # 6. Relocate to .goatee/archive/<change_id>
    archive_dir = root / ".goatee" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = archive_dir / change_id

    if dest_dir.exists():
        raise LifecycleError(f"Target archive destination already exists: {dest_dir}")

    shutil.move(str(change_dir), str(dest_dir))

    # 7. Clean up .goatee/.active if pointing to this change
    active_file = root / ".goatee" / ".active"
    if active_file.is_file():
        cur_active = active_file.read_text(encoding="utf-8").strip()
        if cur_active == change_id:
            try:
                active_file.unlink()
            except OSError:
                pass

    return dest_dir


def archive_change(root: Path, change_id: str, reason: str = "ARCHIVED") -> Path:
    """Explicitly archive a change (e.g. deprecated, superseded, or abandoned).
    
    Returns destination path in .goatee/archive/<change_id>.
    """
    changes_dir = root / ".goatee" / "changes"
    change_dir = changes_dir / change_id
    contract_file = change_dir / "change.md"

    if not change_dir.is_dir() or not contract_file.is_file():
        raise LifecycleError(f"Change '{change_id}' not found at {change_dir}")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    update_contract_state(contract_file, "Lifecycle-Status", reason)
    update_contract_state(contract_file, "Archived-At", now_iso)

    new_hash = compute_contract_hash(contract_file)
    update_contract_state(contract_file, "Contract-SHA256", new_hash)

    archive_dir = root / ".goatee" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = archive_dir / change_id

    if dest_dir.exists():
        raise LifecycleError(f"Target archive destination already exists: {dest_dir}")

    shutil.move(str(change_dir), str(dest_dir))

    active_file = root / ".goatee" / ".active"
    if active_file.is_file():
        cur_active = active_file.read_text(encoding="utf-8").strip()
        if cur_active == change_id:
            try:
                active_file.unlink()
            except OSError:
                pass

    return dest_dir
