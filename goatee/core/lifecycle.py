"""Change lifecycle management: landing accepted changes and archiving retired changes."""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from goatee.core.parser import (
    parse_change_contract,
    parse_metadata_block,
    update_contract_state,
    compute_contract_hash,
    ContractSyntaxError
)
from goatee.core.status import ControlStateError, ReceiptSyntaxError


class LifecycleError(Exception):
    """Raised when a lifecycle operation (land/archive) violates preconditions."""
    pass


def land_change(root: Path, change_id: str) -> Path:
    """Land an accepted change: verify acceptance preconditions, update lifecycle, and move to archive.
    
    Returns destination path in .goatee/archive/<change_id>.
    """
    changes_dir = root / ".goatee" / "changes"
    change_dir = changes_dir / change_id
    contract_file = change_dir / "change.md"
    receipt_file = change_dir / "receipt.md"
    review_file = change_dir / "review.md"

    if not change_dir.is_dir() or not contract_file.is_file():
        raise LifecycleError(f"Change '{change_id}' not found at {change_dir}")

    # 1. Require receipt.md
    if not receipt_file.is_file():
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: delivery receipt missing. Run 'goatee verify {change_id}' first."
        )

    # 2. Require review.md with ACCEPTED verdict
    if not review_file.is_file():
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: human review missing. Run 'goatee attest {change_id}' first."
        )

    review_text = review_file.read_text(encoding="utf-8")
    review_meta = parse_metadata_block(review_text)
    review_verdict = review_meta.get("Review-Verdict", "").strip()

    if review_verdict != "ACCEPTED":
        raise LifecycleError(
            f"Change '{change_id}' cannot be landed: Review-Verdict is '{review_verdict or 'UNKNOWN'}'. Must be 'ACCEPTED'."
        )

    # 3. Update contract state with Lifecycle-Status and Landed-At
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    update_contract_state(contract_file, "Lifecycle-Status", "LANDED")
    update_contract_state(contract_file, "Landed-At", now_iso)

    # Update Contract-SHA256 after lifecycle state change
    new_hash = compute_contract_hash(contract_file)
    update_contract_state(contract_file, "Contract-SHA256", new_hash)

    # 4. Relocate to .goatee/archive/<change_id>
    archive_dir = root / ".goatee" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = archive_dir / change_id

    if dest_dir.exists():
        raise LifecycleError(f"Target archive destination already exists: {dest_dir}")

    shutil.move(str(change_dir), str(dest_dir))

    # 5. Clean up .goatee/.active if pointing to this change
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
