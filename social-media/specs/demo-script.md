# Social Media Publishing — Demo Script

## Architecture Overview (open with this)

"Here's the design: AI and scripts have separate responsibilities.

The core principle: **AI teaches the machine once, then code performs the workflow repeatedly.**

- **AI handles**: content writing, workflow discovery, script generation, script repair, result evaluation
- **Scripts handle**: browser automation, login state reuse, scheduling, verification, retry, logging, screenshots

This means AI doesn't operate the browser every time — only when something changes.

## Folder Structure (show this)

```
social-media/
├── scripts/
│   ├── linkedin.py    ← standalone Playwright scripts
│   └── x.py
├── auth/              ← browser auth state (never committed to git)
├── posts/
│   ├── pending/       ← drafts waiting to publish
│   └── published/     ← archived after success
├── logs/              ← per-platform logs
├── screenshots/       ← captured on every publish
└── specs/             ← workflow discovery notes and selectors
```

## Demo Flow

### Part 1: First Auth Setup (one-time)

```bash
cd /Users/brocode/workspace/skill-pilot

# First run — headed browser opens, log in manually once:
core/engine/.venv/bin/python social-media/scripts/x.py \
  --content-file workspace/social-media/x/demo-draft.md
```

**Talking point**: "First run opens a visible browser. I log in once, and Playwright saves the session. From now on, every run reuses this auth state — no passwords stored in code."

### Part 2: Headless Publish (the real automation)

```bash
core/engine/.venv/bin/python social-media/scripts/x.py \
  --content-file workspace/social-media/x/demo-draft.md --headless
```

**Talking point**: "Now with auth saved, I run headless. No browser window. The script navigates, fills content, clicks Post, verifies, takes a screenshot, and returns structured JSON."

### Part 3: LinkedIn (same pattern)

```bash
core/engine/.venv/bin/python social-media/scripts/linkedin.py \
  --content-file workspace/social-media/linkedin/demo-draft.md
```

## Key Design Decisions to Mention

1. **State machine**: NEW_WORKFLOW → BROWSER_DISCOVERY → SCRIPT_CREATED → SCRIPT_VERIFIED → SCRIPT_REUSED
2. **Failure path**: If verification fails, script retries with fresh selectors. If still failing, AI inspects the page and repairs the script.
3. **Verification contract**: Every script has built-in verification — LinkedIn checks for "Post successful" toast, X checks for empty composer textbox.
4. **Exact-match selectors**: Early bug — "Post" matched "Start a post" as substring. Fixed with exact matching.
5. **Stale ref handling**: Button refs change after text input. Script re-queries fresh locators on each retry.
6. **Anti-bot detection**: X sometimes redirects to graduated-access. Script detects and warns.
