#!/usr/bin/env python3
"""
Script repair engine — diagnose failures, create versioned fixes, manage approval gate.

Usage:
    python social-media/scripts/repair.py --auto x
    python social-media/scripts/repair.py --approve x
    python social-media/scripts/repair.py --reject x --reason "wrong selector"
"""

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from common import (
    load_state, save_state, get_platform_state, update_platform_state,
    append_version_log, now_iso, classify_error,
)

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parents[1]

log = logging.getLogger("repair")
log.setLevel(logging.DEBUG)
sh = logging.StreamHandler(sys.stderr)
sh.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(sh)


def get_latest_version(platform: str) -> int:
    highest = 0
    for f in SCRIPTS_DIR.glob(f"{platform}-v*.py"):
        try:
            v = int(f.stem.split("-v")[1])
            if v > highest:
                highest = v
        except (ValueError, IndexError):
            continue
    return highest


def create_new_version(platform: str) -> tuple[int, Path]:
    current = SCRIPTS_DIR / f"{platform}.py"
    if current.is_symlink():
        current = current.resolve()
    latest = get_latest_version(platform)
    new_v = latest + 1
    new_path = SCRIPTS_DIR / f"{platform}-v{new_v}.py"
    shutil.copy2(current, new_path)
    log.info(f"Created {new_path.name}")
    return new_v, new_path


def apply_repair(version_path: Path, platform: str, error_category: str) -> bool:
    content = version_path.read_text()

    if error_category == "selector-not-found":
        log.warning("selector-not-found requires AI inspection. Marking as NEEDS_AI.")
        update_platform_state(platform, {
            "state": "NEEDS_AI",
            "last_failure": now_iso(),
            "failure_count": get_platform_state(platform).get("failure_count", 0) + 1,
        })
        return False

    elif error_category == "anti-bot":
        old = 'page.wait_for_timeout(3000)'
        new = 'page.wait_for_timeout(10000)  # increased for anti-bot backoff'
        if old in content:
            content = content.replace(old, new, 1)
            version_path.write_text(content)
            append_version_log(platform, version_path.name, "REPAIRED", "anti-bot: increased initial wait to 10s")
            return True
        return False

    elif error_category == "timeout":
        content = content.replace('timeout=15000', 'timeout=30000')
        content = content.replace('timeout=10000', 'timeout=20000')
        version_path.write_text(content)
        append_version_log(platform, version_path.name, "REPAIRED", "timeout: doubled all timeout values")
        return True

    elif error_category == "auth-expired":
        update_platform_state(platform, {
            "state": "AUTH_REQUIRED",
            "last_failure": now_iso(),
        })
        log.error("Auth expired. Human must re-authenticate.")
        return False

    return False


def symlink_latest(platform: str, version_path: Path) -> None:
    link = SCRIPTS_DIR / f"{platform}.py"
    if link.exists() and not link.is_symlink():
        backup = SCRIPTS_DIR / f"{platform}-v{get_latest_version(platform) + 1}.py"
        if not backup.exists():
            shutil.copy2(link, backup)
            append_version_log(platform, backup.name, "BACKUP", "backup before symlink conversion")
    if link.is_symlink() or not link.exists():
        if link.exists():
            link.unlink()
        link.symlink_to(version_path.name)
        log.info(f"Symlink: {platform}.py -> {version_path.name}")


def auto_repair(platform: str) -> int:
    state = get_platform_state(platform)
    error_cat = state.get("last_error_category", "unknown")

    log.info(f"Platform: {platform}, Error category: {error_cat}")

    if state.get("state") == "AWAITING_APPROVAL":
        log.info("A previous repair is awaiting approval. Use --approve or --reject.")
        return 1

    new_v, new_path = create_new_version(platform)
    append_version_log(platform, new_path.name, "CREATED", f"created from {platform}.py for repair")

    repaired = apply_repair(new_path, platform, error_cat)

    if repaired:
        update_platform_state(platform, {
            "state": "AWAITING_APPROVAL",
            "proposed_version": new_path.name,
            "repair_count": state.get("repair_count", 0) + 1,
            "repair_note": f"Auto-repaired {error_cat} error",
        })
        log.info(f"Repair applied to {new_path.name}. Awaiting human approval.")
        log.info(f"  Approve: python social-media/scripts/repair.py --approve {platform}")
        log.info(f"  Reject:  python social-media/scripts/repair.py --reject {platform}")
        return 0
    else:
        log.warning(f"Auto-repair not possible for {error_cat}. Escalating.")
        return 1


def approve_repair(platform: str) -> int:
    state = get_platform_state(platform)
    proposed = state.get("proposed_version")
    if not proposed:
        log.error(f"No pending repair for {platform}.")
        return 1
    proposed_path = SCRIPTS_DIR / proposed
    if not proposed_path.exists():
        log.error(f"Proposed version not found: {proposed}")
        return 1
    symlink_latest(platform, proposed_path)
    update_platform_state(platform, {
        "state": "SCRIPT_VERIFIED",
        "current_version": proposed,
        "awaiting_approval": False,
        "failure_count": 0,
        "last_approved": now_iso(),
    })
    append_version_log(platform, proposed, "APPROVED", "human approved repair")
    log.info(f"Repair approved. {platform}.py -> {proposed}")
    return 0


def reject_repair(platform: str, reason: str = "") -> int:
    state = get_platform_state(platform)
    proposed = state.get("proposed_version")
    if not proposed:
        log.error(f"No pending repair for {platform}.")
        return 1
    current = state.get("current_version", f"{platform}-v1.py")
    current_path = SCRIPTS_DIR / current
    if current_path.exists():
        symlink_latest(platform, current_path)
        log.info(f"Reverted {platform}.py -> {current}")
    update_platform_state(platform, {
        "state": "NEEDS_AI",
        "awaiting_approval": False,
        "last_rejected": now_iso(),
    })
    note = f"REJECTED: {reason}" if reason else "REJECTED"
    append_version_log(platform, proposed, note, "human rejected repair")
    log.info(f"Repair rejected. Platform marked NEEDS_AI.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Social media script repair engine")
    parser.add_argument("--auto", metavar="PLATFORM", help="Auto-diagnose and repair")
    parser.add_argument("--approve", metavar="PLATFORM", help="Approve pending repair")
    parser.add_argument("--reject", metavar="PLATFORM", help="Reject pending repair")
    parser.add_argument("--reason", default="", help="Reason for rejection")
    args = parser.parse_args()

    if args.auto:
        sys.exit(auto_repair(args.auto))
    elif args.approve:
        sys.exit(approve_repair(args.approve))
    elif args.reject:
        sys.exit(reject_repair(args.reject, args.reason))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
