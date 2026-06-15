# Demo Transcript — Social Media Publishing Automation

## Opening

"Hey, I'm going to walk through the social media publishing automation we built. The core design principle is: AI teaches the machine once, then code performs the workflow repeatedly."

"Here's the split: AI handles content writing, workflow discovery, script generation, and evaluation. Normal scripts handle everything else — browser automation, login state, verification, retry, logging, and screenshots. AI does not operate the browser every time. Only when a workflow changes or breaks."

## Command 1 — Folder Structure
`ls social-media/scripts/ social-media/auth/ social-media/specs/`

"Here's the layout. Two standalone Playwright scripts — one for LinkedIn, one for X. Auth state lives in this directory and is gitignored — never committed. Specs capture the selectors and workflow notes from the browser discovery phase."

"The scripts support --content-file for the draft, --mode for publish or verify-only, --headless for invisible runs, and --cdp-port to connect to an existing Chrome session."

## Command 2 — Show a Draft
`cat workspace/social-media/x/demo-draft.md`

"This is what a draft looks like. Just markdown. The script reads this, enforces the 280-character limit for X, and publishes it."

## Command 3 — X Publish via CDP
`core/engine/.venv/bin/python social-media/scripts/x.py --content-file workspace/social-media/x/demo-draft.md --cdp-port 9222`

"This connects to my already-authenticated Chrome on port 9222. No separate login, no password in code. The script navigates to X, finds the composer textbox, fills the content, waits for the Post button to enable, clicks it, and verifies the post by checking that the textbox is empty."

"One thing we fixed: X has a layers overlay that intercepts normal click events. The script falls back to dispatching a click event directly when the overlay blocks it."

"The output is structured JSON: status, platform, screenshot path, and any error. Exit code 0 for success, 1 for failure."

## Command 4 — LinkedIn Publish via CDP
`core/engine/.venv/bin/python social-media/scripts/linkedin.py --content-file workspace/social-media/linkedin/demo-draft.md --cdp-port 9222`

"Same pattern for LinkedIn. Connects via CDP, clicks Start a post, fills the editor, clicks Post, and verifies by checking for the 'Post successful' toast."

"Interesting bug we hit here — the word Post appears in multiple buttons on the page. 'New posts', 'Start a post', 'Post to Anyone', and the actual Post button. The substring match was picking up the wrong one. We fixed it by iterating all buttons and doing an exact text match only on visible, enabled ones."

## Command 5 — Show Results
```
ls social-media/screenshots/x/ social-media/screenshots/linkedin/
cat social-media/logs/linkedin.log | tail -5
```

"Every run saves a timestamped screenshot and a log entry. Both platforms confirmed. The screenshots capture the page state after publishing — success toast on LinkedIn, empty composer on X."

## Closing

"The state machine works like this: NEW_WORKFLOW → BROWSER_DISCOVERY → SCRIPT_CREATED → SCRIPT_VERIFIED → SCRIPT_REUSED. If verification ever fails, we go to BROWSER_REPAIR, AI inspects the page, updates the selectors, and we verify again."

"That's the architecture. AI does thinking, writing, discovery, and repair. Scripts do execution, scheduling, verification, and logging. Teach the machine once, run it a thousand times."
