"""Shared helpers for social media publishing scripts."""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTH_DIR = ROOT / "social-media" / "auth"
POSTS_PUBLISHED = ROOT / "social-media" / "posts" / "published"
POSTS_FAILED = ROOT / "social-media" / "posts" / "failed"
STATE_FILE = AUTH_DIR / "script-state.json"
VERSIONS_LOG = AUTH_DIR / "versions.log"

POSTS_PUBLISHED.mkdir(parents=True, exist_ok=True)
POSTS_FAILED.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("social-common")


def content_hash(text: str) -> str:
    """SHA-256 hex digest of normalized post text."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def load_state() -> dict:
    """Load script-state.json or return empty default."""
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save_state(state: dict) -> None:
    """Write script-state.json atomically."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def get_platform_state(platform: str) -> dict:
    """Get state dict for one platform, with defaults."""
    state = load_state()
    if platform not in state:
        state[platform] = {
            "state": "SCRIPT_VERIFIED",
            "current_version": f"{platform}-v1.py",
            "last_success": None,
            "last_failure": None,
            "failure_count": 0,
            "repair_count": 0,
            "awaiting_approval": False,
            "current_selectors": {},
        }
    return state[platform]


def update_platform_state(platform: str, updates: dict) -> None:
    """Merge updates into one platform's state and save."""
    state = load_state()
    if platform not in state:
        state[platform] = {}
    state[platform].update(updates)
    state[platform]["updated_at"] = now_iso()
    save_state(state)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append_version_log(platform: str, version: str, action: str, note: str) -> None:
    """Append a line to versions.log."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()}  {version}  {action:10s}  {note}\n"
    with open(VERSIONS_LOG, "a") as f:
        f.write(line)


def check_duplicate(text: str, draft_path: str = "", history_path: Path | None = None) -> dict | None:
    """
    Check if content has already been published.
    Returns None if OK to publish, or a dict with reason if duplicate found.
    """
    h = content_hash(text)

    # Check 1: content hash in published post records
    if POSTS_PUBLISHED.exists():
        for record_file in sorted(POSTS_PUBLISHED.glob("*.json")):
            try:
                record = json.loads(record_file.read_text())
                if record.get("content_hash") == h:
                    return {"reason": "content_hash_match", "matched": str(record_file)}
            except (ValueError, OSError):
                continue

    # Check 2: draft path in history
    if history_path and history_path.exists():
        try:
            history = json.loads(history_path.read_text())
            for post in history.get("posts", []):
                if post.get("draft") == draft_path and post.get("status") == "published":
                    return {"reason": "draft_already_published", "matched": draft_path}
        except (ValueError, OSError):
            pass

    return None  # no duplicate found


def classify_error(error_text: str, page_url: str = "") -> str:
    """
    Classify a failure into one of:
    selector-not-found | anti-bot | auth-expired | timeout | unknown
    """
    if "graduated-access" in page_url.lower():
        return "anti-bot"
    if "login" in page_url.lower() or "checkpoint" in page_url.lower():
        return "auth-expired"
    if "Timeout" in error_text and "waiting for" in error_text.lower():
        if "textbox" in error_text.lower() or "button" in error_text.lower():
            return "selector-not-found"
        return "timeout"
    if "Timeout" in error_text:
        return "timeout"
    return "unknown"


def save_debug_bundle(platform: str, error: str, page_html: str = "", screenshot_path: str = "") -> str:
    """Save error log, page HTML, and screenshot reference into a timestamped folder. Returns bundle path."""
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    bundle_dir = POSTS_FAILED / platform / f"debug-{ts}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "error.log").write_text(error)
    if page_html:
        (bundle_dir / "page.html").write_text(page_html)
    if screenshot_path:
        (bundle_dir / "screenshot.ref").write_text(screenshot_path)
    log.info(f"Debug bundle saved to {bundle_dir}")
    return str(bundle_dir)
