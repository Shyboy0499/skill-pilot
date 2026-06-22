---
name: social-marketing
description: Use when publishing social media content to LinkedIn or X, checking posting history, or managing the self-healing automation loop for Playwright publishing scripts.
---

# Social Marketing

Publish social media content via Playwright scripts. Check history, run the script, verify the result. AI does not operate the browser — scripts do.

## When to Use

- User wants to **publish** a draft to LinkedIn or X
- User wants to check **posting history** or frequency
- A publishing script **failed** and needs diagnosis or repair
- User wants to run a **health check** on the automation

## When NOT to Use

- User wants to **write or edit** content → use `social-content` skill instead
- User wants to **repurpose** content across platforms → use `social-content`

## Architecture

```
social-content          social-marketing
┌────────────────┐     ┌───────────────────────────┐
│ Write draft    │ ──▶ │ Check history (no dupes)   │
│ Save to file   │     │ Run Playwright script      │
│                │     │ Verify result              │
│                │     │ Record in history           │
└────────────────┘     └───────────────────────────┘
```

AI writes and checks. Scripts execute. Human approves.

## Instructions

### Step 1: Check History

Read `.skillpilot/social-marketing-history.json`.

- If missing, create: `{"posts": [], "next_scheduled": {}}`
- If last post for this platform was **< 24 hours ago**, warn the user
- Check content hash against `workspace/social-media/posts/published/*.json` for duplicates
- If duplicate found → stop and tell the user

### Step 2: Confirm Content

Display the draft content to the user. Wait for explicit **"Approve"** or **"Publish"** before proceeding.

If the content needs editing, hand off to the `social-content` skill. Do not write content yourself.

### Step 3: Publish via Script

Run the Playwright script:

```bash
core/engine/.venv/bin/python workspace/social-media/scripts/linkedin.py \
  --content-file <draft-path> --cdp-port 9222

core/engine/.venv/bin/python workspace/social-media/scripts/x.py \
  --content-file <draft-path> --cdp-port 9222
```

The script handles: navigation, auth, filling content, clicking Post, verification, screenshots, and structured JSON output. AI does not touch the browser.

### Step 4: Handle Result

**On success** (`"status": "success"`):
- Report: platform, screenshot path, post record saved
- Next recommended posting window (24h from now)

**On failure** (`"status": "failed"`):
- Read the error field from JSON output
- Classify: `selector-not-found` | `anti-bot` | `auth-expired` | `timeout` | `unknown`
- If `auth-expired` → ask user to re-authenticate in Chrome
- If `anti-bot` → wait 5 minutes, retry once
- If `selector-not-found` → run repair: `python workspace/social-media/scripts/repair.py --auto <platform>`
- If repair created a new version → tell user to review and approve

### Step 5: Health Check (Optional)

Run verify-only mode to check selectors without posting:

```bash
core/engine/.venv/bin/python workspace/social-media/scripts/x.py --mode verify-only --cdp-port 9222
```

Returns success if all selectors are valid and auth is active.

## Key Files

| File | Purpose |
|------|---------|
| `workspace/social-media/scripts/x.py` | X/Twitter Playwright publisher |
| `workspace/social-media/scripts/linkedin.py` | LinkedIn Playwright publisher |
| `workspace/social-media/scripts/common.py` | Shared helpers (dedup, state, error classification) |
| `workspace/social-media/scripts/repair.py` | Versioned repair engine (`--auto`, `--approve`, `--reject`) |
| `workspace/social-media/scripts/evaluate.py` | Post performance evaluation |
| `workspace/social-media/auth/` | Playwright browser sessions (never commit) |
| `workspace/social-media/posts/published/` | Post records with metrics and AI evaluation |
| `.skillpilot/social-marketing-history.json` | Publishing history for frequency checks |

## Key Principles

- **Script-first**: Always try the Playwright script before any manual browser work
- **No browser operation by AI**: AI checks history, reviews content, runs scripts via CLI
- **Human-in-the-loop**: Never publish without explicit user approval of the content
- **Dedup before publish**: Content hash check prevents double-posting
- **Versioned repair**: Script repairs create new versions (`x-v2.py`), never overwrite originals
