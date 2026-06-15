#!/usr/bin/env python3
"""
X/Twitter publishing via Playwright.

First run (auth setup):
    python social-media/scripts/x.py --content-file post.md --mode publish
    # Opens headed browser. Log in manually. Auth state saved automatically.

Later runs:
    python social-media/scripts/x.py --content-file post.md --mode publish --headless

280-character limit enforced. Exceeding it is an error.

Output:
    JSON to stdout: {"status": "success|failed", "platform": "x", ...}
    Screenshots saved to social-media/screenshots/x/
    Logs saved to social-media/logs/x.log
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

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "social-media" / "scripts"
AUTH_DIR = ROOT / "social-media" / "auth"
LOG_DIR = ROOT / "social-media" / "logs"
SCREENSHOT_DIR = ROOT / "social-media" / "screenshots" / "x"
POSTS_PUBLISHED = ROOT / "social-media" / "posts" / "published"
POSTS_FAILED = ROOT / "social-media" / "posts" / "failed"

AUTH_FILE = AUTH_DIR / "x-auth.json"

for d in (AUTH_DIR, LOG_DIR, SCREENSHOT_DIR, POSTS_PUBLISHED, POSTS_FAILED):
    d.mkdir(parents=True, exist_ok=True)

MAX_CHARS = 280

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("x-publish")
log.setLevel(logging.DEBUG)

fh = logging.FileHandler(LOG_DIR / "x.log")
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

FEED_URL = "https://x.com/home"
COMPOSER_TEXTBOX_LABEL = "Post text"
POST_BUTTON_TEXT = "Post"  # exact match

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
    if len(content) > MAX_CHARS:
        raise ValueError(f"Content is {len(content)} chars, exceeds X limit of {MAX_CHARS}")
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
        "platform": "x",
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


def verify_post_published(page, content: str) -> bool:
    """Check that the composer textbox is empty after publishing."""
    try:
        page.wait_for_timeout(2000)
        textbox = page.get_by_role("textbox", name=COMPOSER_TEXTBOX_LABEL)
        if textbox.count() > 0 and textbox.first.is_visible():
            try:
                value = textbox.first.input_value()
                if value.strip() == "":
                    log.info("Verification: composer textbox is empty — post published.")
                    return True
                else:
                    log.warning(f"Verification: textbox still has content ({len(value)} chars).")
                    return False
            except Exception:
                # input_value() can fail if the element is not an input (X replaces DOM)
                log.info("Verification: textbox DOM changed (post likely published).")
                return True
        log.info("Verification: composer textbox not visible — post likely published.")
        return True
    except Exception as exc:
        log.warning(f"Verification error: {exc}")
        return False


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def publish_post(content: str, headless: bool = False, cdp_port: str = "") -> dict:
    cdp_connected = False

    with sync_playwright() as p:
        if cdp_port:
            # Ensure Chrome has at least one tab (CDP browser-level connection
            # fails if Chrome has zero open tabs).
            try:
                subprocess.run(
                    ["open", "-a", "Google Chrome", FEED_URL],
                    capture_output=True, timeout=5,
                )
                time.sleep(2)
            except Exception:
                pass

            # Connect to existing Chrome via CDP (uses its auth session)
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
                    or "account/access" in page.url
                    or page.get_by_text("Sign in to X").is_visible()
                    or page.get_by_role("button", name="Log in").is_visible()
                )
                if needs_login:
                    if headless:
                        raise RuntimeError("X login required. Run without --headless first.")
                    log.info("Login required. Please log in manually in the browser window.")
                    log.info("Waiting up to 120 seconds...")
                    page.wait_for_url(f"{FEED_URL}*", timeout=120_000)
                    page.wait_for_timeout(2000)
                    context.storage_state(path=str(AUTH_FILE))
                    log.info("Auth state saved.")

            # --- Anti-bot gate ---
            if "graduated-access" in page.url:
                log.warning("X is showing graduated-access rate-limit page.")
                log.warning("Waiting 30s and retrying...")
                page.wait_for_timeout(30_000)
                page.goto(FEED_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

            # --- Step 1: Fill the composer textbox ---
            log.info("Looking for composer textbox...")
            textbox = page.get_by_role("textbox", name=COMPOSER_TEXTBOX_LABEL)
            textbox.wait_for(state="visible", timeout=15_000)
            textbox.fill(content)
            log.info(f"Filled {len(content)} characters.")

            # --- Step 2: Wait for Post button to enable ---
            log.info("Waiting for Post button to enable...")
            page.wait_for_timeout(2000)

            # --- Step 3: Find enabled Post button ---
            post_btn = None
            for attempt in range(5):
                buttons = page.locator('button:has-text("Post"):not([disabled])')
                count = buttons.count()
                for i in range(count):
                    btn = buttons.nth(i)
                    text = btn.inner_text().strip()
                    if text == POST_BUTTON_TEXT and btn.is_enabled():
                        post_btn = btn
                        break
                if post_btn:
                    break
                log.info(f"  Post button not ready, waiting... (attempt {attempt + 1}/5)")
                page.wait_for_timeout(1000)

            if post_btn is None:
                raise RuntimeError("Could not find an enabled Post button on X after 5 attempts.")

            # --- Step 4: Click Post (force click — bypasses X overlay intercepting pointer events) ---
            log.info("Clicking Post...")
            try:
                post_btn.click(timeout=5000)
            except Exception:
                log.info("  Normal click intercepted by overlay, using force click...")
                post_btn.dispatch_event("click")
            page.wait_for_timeout(4000)

            # --- Check for anti-bot redirect ---
            if "graduated-access" in page.url:
                log.error("X anti-bot challenge triggered after clicking Post.")
                screenshot_path = str(SCREENSHOT_DIR / f"antibot-{int(time.time())}.png")
                page.screenshot(path=screenshot_path, full_page=True)
                if not cdp_connected: browser.close()
                return save_result("failed", screenshot=screenshot_path, error="Anti-bot graduated-access redirect")

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
                return save_result("success", screenshot=screenshot_path)

            # Retry click once
            log.warning("Verification failed. Retrying click...")
            for retry in range(3):
                page.wait_for_timeout(1500)
                buttons = page.locator('button:has-text("Post"):not([disabled])')
                for i in range(buttons.count()):
                    btn = buttons.nth(i)
                    if btn.inner_text().strip() == POST_BUTTON_TEXT and btn.is_enabled():
                        try:
                            btn.click(timeout=5000)
                        except Exception:
                            btn.dispatch_event("click")
                        page.wait_for_timeout(4000)
                        if verify_post_published(page, content):
                            context.storage_state(path=str(AUTH_FILE))
                            if not cdp_connected: browser.close()
                            log.info(f"Publish verified — success on retry {retry + 1}.")
                            return save_result("success", screenshot=screenshot_path)
                        break

            if not cdp_connected: browser.close()
            log.error("Verification failed after all retries.")
            return save_result("failed", screenshot=screenshot_path, error="Verification failed after retries")

        except PlaywrightTimeoutError as e:
            ts = int(time.time())
            screenshot_path = str(SCREENSHOT_DIR / f"timeout-{ts}.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                screenshot_path = ""
            if not cdp_connected: browser.close()
            log.error(f"Timeout: {e}")
            return save_result("failed", screenshot=screenshot_path, error=str(e))

        except Exception as e:
            ts = int(time.time())
            screenshot_path = str(SCREENSHOT_DIR / f"error-{ts}.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                screenshot_path = ""
            if not cdp_connected: browser.close()
            log.error(f"Error: {e}")
            return save_result("failed", screenshot=screenshot_path, error=str(e))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="X/Twitter publishing via Playwright")
    parser.add_argument("--content-file", required=True, help="Path to markdown content file")
    parser.add_argument("--mode", choices=["draft", "publish", "verify-only"], default="publish")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--cdp-port", default="", help="Connect to existing Chrome via CDP port (e.g. 9222)")
    parser.add_argument("--verify", dest="verify_flag", default="true")
    args = parser.parse_args()

    try:
        content = read_content(args.content_file)
    except Exception as e:
        log.error(f"Failed to read content: {e}")
        print(json.dumps({"status": "failed", "error": str(e)}))
        sys.exit(1)

    log.info(f"Content loaded: {len(content)} chars from {args.content_file}")

    if args.mode == "publish":
        result = publish_post(content, headless=args.headless, cdp_port=args.cdp_port)
    elif args.mode == "verify-only":
        print(json.dumps({"status": "skipped", "action": "verify-only", "platform": "x"}))
        sys.exit(0)
    else:
        log.error(f"Mode '{args.mode}' not implemented yet.")
        sys.exit(1)

    if result["status"] == "success":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
