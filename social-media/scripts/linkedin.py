#!/usr/bin/env python3
"""
LinkedIn publishing via Playwright.

First run (auth setup):
    python social-media/scripts/linkedin.py --content-file post.md --mode publish
    # Opens headed browser. Log in manually. Auth state saved automatically.

Later runs:
    python social-media/scripts/linkedin.py --content-file post.md --mode publish --headless

Output:
    JSON to stdout: {"status": "success|failed", "platform": "linkedin", ...}
    Screenshots saved to social-media/screenshots/linkedin/
    Logs saved to social-media/logs/linkedin.log
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from common import (
    check_duplicate, classify_error, save_debug_bundle,
    update_platform_state, append_version_log,
    content_hash, now_iso, get_platform_state,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "workspace" / "social-media" / "scripts"
AUTH_DIR = ROOT / "workspace" / "social-media" / "auth"
LOG_DIR = ROOT / "workspace" / "social-media" / "logs"
SCREENSHOT_DIR = ROOT / "workspace" / "social-media" / "screenshots" / "linkedin"
POSTS_PUBLISHED = ROOT / "workspace" / "social-media" / "posts" / "published"
POSTS_FAILED = ROOT / "workspace" / "social-media" / "posts" / "failed"

AUTH_FILE = AUTH_DIR / "linkedin-auth.json"

for d in (AUTH_DIR, LOG_DIR, SCREENSHOT_DIR, POSTS_PUBLISHED, POSTS_FAILED):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("linkedin-publish")
log.setLevel(logging.DEBUG)

fh = logging.FileHandler(LOG_DIR / "linkedin.log")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

sh = logging.StreamHandler(sys.stderr)
sh.setLevel(logging.INFO)
sh.setFormatter(logging.Formatter("%(message)s"))

log.addHandler(fh)
log.addHandler(sh)

# ---------------------------------------------------------------------------
# Selectors (captured from browser discovery)
# ---------------------------------------------------------------------------

FEED_URL = "https://www.linkedin.com/feed/"
POST_TRIGGER_TEXT = "Start a post"  # exact match button
COMPOSER_TEXTBOX_LABEL = "Text editor for creating content"
POST_BUTTON_TEXT = "Post"  # exact match (NOT substring of "Start a post")
SUCCESS_INDICATOR = "Post successful"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_content(path: str) -> str:
    full = (ROOT / path).expanduser().resolve()
    if not full.exists():
        full = Path(path).resolve()
    content = full.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Content file is empty: {full}")
    return content


def save_result(
    status: str,
    url: str | None = None,
    screenshot: str | None = None,
    error: str | None = None,
    action: str = "publish_post",
) -> dict:
    result = {
        "status": status,
        "action": action,
        "platform": "linkedin",
        "url": url,
        "screenshot": screenshot,
        "error": error,
    }
    print(json.dumps(result, indent=2))
    return result


def timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_post_published(page, short_text: str) -> bool:
    """Check that the composer modal closed and success toast appeared."""
    try:
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        if SUCCESS_INDICATOR in body:
            log.info("Verification: 'Post successful' toast found.")
            return True
        # Fallback: composer modal should be gone
        modal = page.query_selector('[role="dialog"]')
        if modal is None:
            log.info("Verification: composer modal closed (fallback).")
            return True
        log.warning("Verification: composer modal still open, no success toast.")
        return False
    except Exception as exc:
        log.warning(f"Verification error: {exc}")
        return False


# ---------------------------------------------------------------------------
# Verify-only (health check)
# ---------------------------------------------------------------------------


def verify_only_linkedin(headless: bool = False, cdp_port: str = "") -> dict:
    """Health-check: verify browser auth and 'Start a post' button visibility without posting."""
    cdp_connected = False

    with sync_playwright() as p:
        if cdp_port:
            try:
                subprocess.run(
                    ["open", "-a", "Google Chrome", FEED_URL],
                    capture_output=True, timeout=5,
                )
                time.sleep(2)
            except Exception:
                pass

            try:
                log.info(f"Connecting to Chrome via CDP port {cdp_port}...")
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                context = browser.contexts[0]
                page = context.new_page()
                cdp_connected = True
            except Exception as e:
                log.warning(f"CDP connection failed: {e}")
                log.warning("Falling back to own browser.")

        if not cdp_connected:
            browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
            context_kwargs: dict = {}
            if AUTH_FILE.exists():
                try:
                    context_kwargs["storage_state"] = str(AUTH_FILE)
                    log.info("Loaded auth state from disk.")
                except Exception:
                    log.warning("Could not load auth state, starting fresh.")
            context = browser.new_context(**context_kwargs)
            page = context.new_page()

        try:
            log.info(f"[verify-only] Navigating to {FEED_URL}...")
            page.goto(FEED_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Auth check
            if "login" in page.url or "checkpoint" in page.url:
                log.error("[verify-only] Auth expired — URL contains login/checkpoint redirect.")
                if not cdp_connected:
                    browser.close()
                return save_result("failed", error="auth-expired", action="verify-only")

            # "Start a post" button check
            start_btn = page.get_by_text(POST_TRIGGER_TEXT, exact=True).first
            try:
                start_btn.wait_for(state="visible", timeout=10_000)
            except PlaywrightTimeoutError:
                log.error('[verify-only] "Start a post" button not visible.')
                if not cdp_connected:
                    browser.close()
                return save_result("failed", error="selector-not-found: Start a post button", action="verify-only")

            log.info('[verify-only] "Start a post" button visible — health check passed.')
            update_platform_state("linkedin", {
                "last_health_check": now_iso(),
                "failure_count": 0,
            })

            if not cdp_connected:
                browser.close()
            return save_result("success", action="verify-only")

        except Exception as e:
            log.error(f"[verify-only] Error: {e}")
            if not cdp_connected:
                try:
                    browser.close()
                except Exception:
                    pass
            return save_result("failed", error=str(e), action="verify-only")


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def publish_post(content: str, headless: bool = False, cdp_port: str = "") -> dict:
    short_text = content[:60].replace("\n", " ")
    cdp_connected = False

    with sync_playwright() as p:
        if cdp_port:
            # Ensure Chrome has at least one tab open (browser-level CDP
            # connection fails if Chrome has zero open tabs).
            try:
                subprocess.run(
                    ["open", "-a", "Google Chrome", FEED_URL],
                    capture_output=True, timeout=5,
                )
                time.sleep(2)
            except Exception:
                pass

            try:
                log.info(f"Connecting to Chrome via CDP port {cdp_port}...")
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                context = browser.contexts[0]
                page = context.new_page()
                cdp_connected = True
            except Exception as e:
                log.warning(f"CDP connection failed: {e}")
                log.warning("Falling back to own browser.")

        if not cdp_connected:
            browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
            context_kwargs: dict = {}
            if AUTH_FILE.exists():
                try:
                    context_kwargs["storage_state"] = str(AUTH_FILE)
                    log.info("Loaded auth state from disk.")
                except Exception:
                    log.warning("Could not load auth state, starting fresh.")
            context = browser.new_context(**context_kwargs)
            page = context.new_page()

        try:
            # --- Navigate ---
            log.info(f"Navigating to {FEED_URL}...")
            page.goto(FEED_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # --- Auth check (skip in CDP mode — already authenticated) ---
            if not cdp_port:
                needs_login = (
                    "login" in page.url
                    or "checkpoint" in page.url
                    or page.get_by_text("Sign in").first.is_visible()
                )
                if needs_login:
                    if headless:
                        raise RuntimeError("LinkedIn login required. Run without --headless first.")
                    log.info("Login required. Please log in manually in the browser window.")
                    log.info("Waiting up to 120 seconds...")
                    page.wait_for_url(f"{FEED_URL}*", timeout=120_000)
                    page.wait_for_timeout(2000)
                    context.storage_state(path=str(AUTH_FILE))
                    log.info("Auth state saved.")

            # --- Dedup gate ---
            HISTORY_PATH = ROOT / ".skillpilot" / "social-marketing-history.json"
            dup = check_duplicate(content, draft_path="", history_path=HISTORY_PATH)
            if dup:
                log.warning(f"Deduplication: {dup['reason']} — {dup.get('matched', '')}")
                log.warning("Skipping publish — content already published.")
                if not cdp_connected:
                    try:
                        browser.close()
                    except Exception:
                        pass
                return save_result(
                    "skipped",
                    error=f"Duplicate: {dup['reason']}",
                    action="publish_post",
                )

            # --- Step 1: Click "Start a post" ---
            log.info('Looking for "Start a post" button...')
            start_btn = page.get_by_text(POST_TRIGGER_TEXT, exact=True).first
            start_btn.wait_for(state="visible", timeout=15_000)
            try:
                start_btn.click(timeout=5000)
            except Exception:
                start_btn.dispatch_event("click")
            page.wait_for_timeout(2000)
            log.info("Clicked post trigger. Composer should be open.")

            # --- Step 2: Fill content ---
            log.info("Filling post content...")
            textbox = page.get_by_role("textbox", name=COMPOSER_TEXTBOX_LABEL)
            textbox.wait_for(state="visible", timeout=15_000)
            textbox.fill(content)
            page.wait_for_timeout(1500)
            log.info(f"Filled {len(content)} characters.")

            # --- Step 3: Click Post ---
            log.info('Looking for "Post" button...')
            # Find the real Post button: exact text match, visible, enabled.
            # Avoid substring matches: "New posts", "Start a post", "Post to Anyone"
            post_btn = None
            for btn in page.locator("button").all():
                try:
                    if btn.inner_text().strip() == POST_BUTTON_TEXT and btn.is_visible() and btn.is_enabled():
                        post_btn = btn
                        break
                except Exception:
                    continue
            if post_btn is None:
                raise RuntimeError("Could not find an enabled Post button on LinkedIn")
            log.info("Found Post button.")
            try:
                post_btn.click(timeout=5000)
            except Exception:
                post_btn.dispatch_event("click")
            log.info("Clicked Post.")
            page.wait_for_timeout(4000)

            # --- Verification ---
            log.info("Verifying...")
            ts = int(time.time())
            screenshot_path = str(SCREENSHOT_DIR / f"published-{ts}.png")
            page.screenshot(path=screenshot_path, full_page=True)

            ok = verify_post_published(page, content)
            if ok:
                context.storage_state(path=str(AUTH_FILE))
                if not cdp_connected: browser.close()
                log.info("Publish verified — success.")

                # Save post record for dedup and content improvement
                ts = int(time.time())
                record = {
                    "post_id": f"linkedin-{ts}",
                    "platform": "linkedin",
                    "published_at": now_iso(),
                    "content_file": "",
                    "content": content,
                    "hook": content.split("\n")[0][:120] if content else "",
                    "call_to_action": "",
                    "url": "",
                    "screenshot": screenshot_path,
                    "content_hash": content_hash(content),
                    "metrics": {"fetched_at": None, "likes": 0, "comments": 0, "reposts": 0, "impressions": 0},
                    "ai_evaluation": {"evaluated_at": None, "hook_effective": None, "too_broad": None, "too_salesy": None, "showed_insight": None, "clear_audience": None, "invited_reply": None, "what_to_test_next": None},
                }
                record_path = POSTS_PUBLISHED / f"linkedin-{ts}.json"
                record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
                log.info(f"Post record saved: {record_path.name}")

                return save_result("success", screenshot=screenshot_path)

            if not cdp_connected: browser.close()
            log.error("Verification failed after publish.")
            return save_result("failed", screenshot=screenshot_path, error="Verification failed")

        except PlaywrightTimeoutError as e:
            ts = int(time.time())
            screenshot_path = str(SCREENSHOT_DIR / f"timeout-{ts}.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                screenshot_path = ""
            error_category = classify_error(str(e), page.url if hasattr(page, 'url') else "")
            bundle = save_debug_bundle("linkedin", str(e), page.content() if hasattr(page, 'content') else "", screenshot_path)
            update_platform_state("linkedin", {
                "last_failure": now_iso(),
                "last_error": str(e),
                "last_error_category": error_category,
                "failure_count": get_platform_state("linkedin").get("failure_count", 0) + 1,
            })
            log.error(f"Error [{error_category}]: {e}")
            if not cdp_connected: browser.close()
            return save_result("failed", screenshot=screenshot_path, error=str(e))

        except Exception as e:
            ts = int(time.time())
            screenshot_path = str(SCREENSHOT_DIR / f"error-{ts}.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                screenshot_path = ""
            error_category = classify_error(str(e), page.url if hasattr(page, 'url') else "")
            bundle = save_debug_bundle("linkedin", str(e), page.content() if hasattr(page, 'content') else "", screenshot_path)
            update_platform_state("linkedin", {
                "last_failure": now_iso(),
                "last_error": str(e),
                "last_error_category": error_category,
                "failure_count": get_platform_state("linkedin").get("failure_count", 0) + 1,
            })
            log.error(f"Error [{error_category}]: {e}")
            if not cdp_connected: browser.close()
            return save_result("failed", screenshot=screenshot_path, error=str(e))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="LinkedIn publishing via Playwright")
    parser.add_argument("--content-file", default="", help="Path to markdown content file")
    parser.add_argument("--mode", choices=["draft", "publish", "verify-only"], default="publish")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--cdp-port", default="", help="Connect to existing Chrome via CDP port (e.g. 9222)")
    parser.add_argument("--verify", dest="verify_flag", default="true")
    args = parser.parse_args()

    if args.mode == "verify-only":
        result = verify_only_linkedin(args.headless, args.cdp_port)
        sys.exit(0 if result["status"] == "success" else 1)

    if not args.content_file:
        log.error("--content-file is required for mode '%s'", args.mode)
        print(json.dumps({"status": "failed", "error": "--content-file is required"}))
        sys.exit(1)

    try:
        content = read_content(args.content_file)
    except Exception as e:
        log.error(f"Failed to read content: {e}")
        print(json.dumps({"status": "failed", "error": str(e)}))
        sys.exit(1)

    log.info(f"Content loaded: {len(content)} chars from {args.content_file}")

    if args.mode == "publish":
        result = publish_post(content, headless=args.headless, cdp_port=args.cdp_port)
    else:
        log.error(f"Mode '{args.mode}' not implemented yet.")
        sys.exit(1)

    if result["status"] == "success":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
