# Social Media Automation — Closed-Loop Design

## Skill Declaration

The workflow uses two skills with clear boundaries:

```
social-media-content          social-marketing
┌──────────────────┐         ┌──────────────────────────┐
│ Write/edit draft │  ────▶  │ Review draft              │
│ Save to pending/ │         │ Check history (no dupes)  │
│                  │         │ Execute script via CLI:    │
│                  │         │   linkedin.py / x.py      │
│                  │         │ Verify result             │
│                  │         │ Record in history          │
└──────────────────┘         └──────────────────────────┘
```

- **`social-media-content`**: AI writes and edits content. Saves drafts to `social-media/posts/pending/`. This is the creative step.
- **`social-marketing`**: AI checks posting frequency via history, reviews content appropriateness, then invokes the Playwright script via CLI. The script handles everything else — browser, publish, verification, logging. AI does not operate the browser.

This keeps the supervisor's architecture intact: AI writes and checks, script executes.

## What We Have Now

```
WRITE DRAFT  →  RUN SCRIPT  →  PUBLISH  →  VERIFY  →  DONE
                                    ↑___________|
                                    retry if fail
```

This is a single-pass pipeline with basic retry. It works, but when something breaks (selector changes, anti-bot triggers, auth expires), a human has to notice, diagnose, and fix it. And there's no feedback from results back into content quality.

## What We're Adding: Three Loops

### Loop 1 — Self-Healing

```
PUBLISH  →  VERIFY  →  FAIL  →  DIAGNOSE  →  REPAIR (-v2)  →  MANUAL CHECK  →  RETRY  →  VERIFY  →  PASS
                                           ↑                                    |
                                           |________ escalate to AI ___________|
```

**Deduplication gate (runs before every publish):**

Before any script clicks "Post", it checks whether the same content was already published:

| Check | How |
|-------|-----|
| Content hash | SHA-256 of the post text. Compare against `social-media/posts/published/*.json` history. If hash exists → skip, log "already published" |
| Draft path | Check if `{draft-name}.md` appears in `social-marketing-history.json` with status "published". If yes → skip |
| Recent duplicate | Search last 10 history entries for >90% text similarity. If found → warn, require manual override |

This prevents the scheduler from publishing the same draft twice if something goes wrong with the history update after a successful post.

**When verification fails**, instead of just returning `{"status": "failed"}`, the system does:

| Step | What happens | Who |
|------|-------------|-----|
| 1. Capture | Screenshot + page HTML + error log saved to `social-media/screenshots/{platform}/failed/` | Script |
| 2. Diagnose | Classify failure: selector-not-found / anti-bot-gate / auth-expired / timeout / unknown | Script (heuristic rules) |
| 3. Repair | If selector-not-found: re-scan page for closest matching element, update selector in script. If anti-bot: wait 5 min, retry. If auth: flag for human. | Script (simple cases) or AI (complex cases) |
| 4. Retry | Run the repaired script again, up to 2 repair cycles | Script |
| 5. Escalate | If repair fails twice, save full debug bundle and notify human/AI | Script → AI |

**Diagnosis rules (heuristic, no AI needed):**

| Symptom | Diagnosis | Action |
|---------|-----------|--------|
| `Timeout waiting for textbox "Post text"` | Selector changed or anti-bot redirect | Re-scan page, update selector |
| `graduated-access` in URL | X anti-bot gate | Wait 5 min, retry |
| `login` in URL after navigation | Auth expired | Flag: needs re-authentication |
| `Timeout waiting for button "Post"` after fill | Button selector changed or content validation blocking | Re-scan page, try alternative button selectors |
| JSON output status = "failed" with unknown error | Unknown | Save debug bundle, escalate to AI |

**Repair strategies:**

| Case | Strategy |
|------|----------|
| Selector not found | `page.content()` → scan for similar ARIA roles/text → find best candidate → create **new version** of script (e.g. `x-v2.py`) with updated SELECTOR constant → save repair note |
| Button not enabled after fill | Try `page.keyboard.press("Enter")` as alternative submit, or click body first to trigger validation |
| Anti-bot gate | Exponential backoff: wait 1min → retry → wait 5min → retry → escalate |
| Auth expired | Save screenshot, log error, mark script state as AUTH_REQUIRED, stop — needs human |

**Versioning — scripts are never overwritten:**

When a repair is made, the script is saved as a new version rather than mutating the original:

```
social-media/scripts/
├── linkedin.py          ← symlink to latest version
├── linkedin-v1.py       ← original (first successful browser discovery)
├── linkedin-v2.py       ← repair: updated Post button selector
├── linkedin-v3.py       ← repair: updated composer textbox selector
├── x.py                 ← symlink to latest version
├── x-v1.py              ← original
├── x-v2.py              ← repair: added overlay bypass
└── versions.log         ← log of what changed per version
```

`versions.log` records each change:
```
2026-06-15 09:00  x-v1.py       CREATED   Initial browser discovery, all selectors verified
2026-06-17 14:22  x-v2.py       REPAIRED  Post button selector changed: "Post" → "Post" [data-testid="tweetButtonInline"]
2026-06-20 08:15  linkedin-v2.py REPAIRED  Composer textbox label changed: "Text editor..." → "Write your post..."
```

This means:
- The latest working version is always available via the symlink
- Previous versions are preserved for comparison
- You can revert to an older version if a repair made things worse
- `versions.log` gives a human-readable history of what changed and why

**Manual check gate (after every auto-repair):**

After the system repairs a script and re-tests it, a human must approve before it goes back into production:

```
REPAIR → RETRY → VERIFY PASS → MARK AS "AWAITING APPROVAL"
                                    ↓
                            Human reviews repair note
                           and versions.log diff
                                    ↓
                     ┌──────────────┴──────────────┐
                     │ APPROVE                      │ REJECT
                     │ Symlink → new version        │ Revert to previous version
                     │ Resume normal operation      │ Escalate to AI for manual repair
                     └──────────────────────────────┘
```

The approval can be as simple as:
```bash
# Human approves the repair:
python social-media/scripts/repair.py --approve x
# Human rejects:
python social-media/scripts/repair.py --reject x --reason "wrong selector"
```

This ensures the loop is automated but not unsupervised. A human always signs off on script changes before they go live.

**Script state file** (`social-media/auth/script-state.json`):

```json
{
  "x": {
    "state": "SCRIPT_VERIFIED",
    "current_version": "x-v2.py",
    "last_success": "2026-06-15T09:00:00+10:00",
    "last_failure": null,
    "failure_count": 0,
    "repair_count": 1,
    "awaiting_approval": false,
    "current_selectors": {
      "composer_textbox": "Post text",
      "post_button": "Post"
    }
  },
  "linkedin": {
    "state": "AWAITING_APPROVAL",
    "current_version": "linkedin-v2.py",
    "proposed_version": "linkedin-v3.py",
    "last_success": "2026-06-15T09:01:00+10:00",
    "last_failure": "2026-06-17T14:22:00+10:00",
    "failure_count": 1,
    "repair_count": 2,
    "awaiting_approval": true,
    "repair_note": "Post button selector changed: 'Post' not found, replaced with button[data-testid='share-button']",
    "current_selectors": {
      "post_trigger": "Start a post",
      "composer_textbox": "Text editor for creating content",
      "post_button": "Post"
    }
  }
}
```

---

### Loop 2 — Health Check (Scheduled Verify-Only)

```
SCHEDULER (every N hours)  →  RUN SCRIPT --mode verify-only  →  CHECK RESULT
                                    ↓                                ↓
                                  PASS: nothing to do          FAIL: trigger Loop 1 (Self-Healing)
```

**Purpose**: Catch broken automation before a real post needs to go out. If LinkedIn changes their UI on Tuesday and the next scheduled post is Friday, the health check catches it Tuesday so there's time to repair.

**How it works:**

| Component | Detail |
|-----------|--------|
| Trigger | APScheduler job, configurable interval (default: every 6 hours) |
| Action | Runs `linkedin.py --mode verify-only` and `x.py --mode verify-only` |
| Verify-only mode | Navigates to feed, checks all selectors are still valid, checks login is still active. Does NOT post anything. |
| On pass | Updates `script-state.json`: `last_health_check = now()`, resets `failure_count = 0` |
| On fail | Triggers Loop 1 (Self-Healing) for that platform |
| Notification | If repair fails: log to `social-media/logs/health.log`, optional webhook/email |

**Config** (`config/social-scheduler.json5`):

```json5
{
  enabled: true,
  schedule: "0 9 * * *",           // daily publish
  platforms: ["linkedin", "x"],
  health_check: {
    enabled: true,
    interval_hours: 6,             // run verify-only every 6 hours
    auto_repair: true,             // trigger self-healing on failure
    max_repair_attempts: 2,        // before escalating to human
    notify_on_escalation: true,
  },
}
```

---

### Loop 3 — Content Improvement

```
PUBLISH  →  WAIT (24-48h)  →  FETCH METRICS  →  AI EVALUATES  →  SUGGESTS IMPROVEMENT
                                  ↑                                        |
                                  |___________ next post applies __________|
```

**Purpose**: Each post teaches the next one. The system learns what hooks, topics, and formats perform best on each platform.

**What gets tracked** (saved to `social-media/posts/published/{post-id}.json`):

```json
{
  "post_id": "linkedin-2026-06-15-001",
  "platform": "linkedin",
  "published_at": "2026-06-15T09:00:00+10:00",
  "content_file": "posts/pending/remote-teams.md",
  "hook": "Three things I've learned managing remote engineering teams",
  "topic": "engineering management",
  "angle": "practical lessons",
  "call_to_action": "What would you add?",
  "url": "https://www.linkedin.com/feed/update/...",
  "screenshot": "screenshots/linkedin/published-xxx.png",
  "metrics": {
    "fetched_at": "2026-06-17T09:00:00+10:00",
    "likes": 0,
    "comments": 0,
    "reposts": 0,
    "impressions": 0
  },
  "ai_evaluation": {
    "evaluated_at": null,
    "hook_effective": null,
    "too_broad": null,
    "too_salesy": null,
    "showed_insight": null,
    "clear_audience": null,
    "invited_reply": null,
    "what_to_test_next": null
  }
}
```

**AI evaluation prompt** (runs 48h after publish, only when metrics are available):

```
You are evaluating a {platform} post's performance.

Post content: {content}
Hook: {hook}
Topic: {topic}
Metrics: {likes} likes, {comments} comments, {reposts} reposts, {impressions} impressions

Evaluate:
1. Did the hook make the target reader care? (yes/no/partial)
2. Was the post too broad? (yes/no)
3. Was it too sales-like? (yes/no)
4. Did it show real insight or just generic advice? (insight/generic)
5. Was there a clear target audience? (yes/no)
6. Did the call-to-action invite genuine replies? (yes/no)
7. What specific change should we test in the next post?
   (e.g., "stronger hook", "more specific audience", "add a counterintuitive take")

Return as JSON.
```

**How improvement flows:**

```
Post A published  →  48h later, AI evaluates  →  "Hook was weak, audience too broad"
                                                    ↓
Post B drafted    →  AI applies lesson: stronger hook, niche audience
                                                    ↓
Post B published  →  48h later, AI evaluates  →  "Better engagement, test adding data point next"
                                                    ↓
Post C drafted    →  AI applies lesson: includes specific statistic
```

The AI's role: reads the evaluation of the last 5 posts, identifies patterns, and writes the next draft with improvements applied. The human approves before publishing.

---

## The Full System: How All Three Loops Connect

```
                          ┌──────────────────────────┐
                          │     SCHEDULER (daily)     │
                          │  9am Sydney, scans        │
                          │  content calendar for     │
                          │  due items                │
                          └──────────┬───────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              ┌──────────┐   ┌──────────┐   ┌──────────┐
              │ Publish  │   │ Publish  │   │ Publish  │
              │ LinkedIn │   │    X     │   │   ...    │
              └────┬─────┘   └────┬─────┘   └────┬─────┘
                   │              │              │
                   ▼              ▼              ▼
              ┌──────────────────────────────────────┐
              │           VERIFY + LOG               │
              └──────────┬───────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         ┌─────────┐          ┌─────────┐
         │  PASS   │          │  FAIL   │
         └────┬────┘          └────┬────┘
              │                    │
              ▼                    ▼
    ┌─────────────────┐   ┌──────────────────┐
    │ Record success   │   │ LOOP 1:          │
    │ Schedule eval    │   │ Self-Healing     │
    │ (48h from now)   │   │ Diagnose → Repair│
    └────────┬────────┘   │ → Retry → Verify │
             │            └────────┬─────────┘
             ▼                     │
    ┌─────────────────┐   ┌───────┴──────────┐
    │ LOOP 3:          │   │ PASS: record fix │
    │ Content Improve  │   │ FAIL: escalate   │
    │ 48h: fetch       │   │ to AI/human      │
    │ metrics → AI     │   └──────────────────┘
    │ evaluates →      │
    │ feeds next draft │
    └──────────────────┘

    ┌──────────────────────────────────────────┐
    │ LOOP 2: Health Check (every 6h)          │
    │ Runs scripts in verify-only mode.        │
    │ If broken → triggers Loop 1.             │
    │ Runs independent of scheduled publishes. │
    └──────────────────────────────────────────┘
```

## AI Breakpoints — When AI Gets Involved

AI does NOT run in every loop. AI is expensive and slow. The system only calls AI at specific breakpoints:

| Trigger | AI's job | Frequency |
|---------|----------|-----------|
| Self-healing failed twice | Inspect debug bundle, write new selector/workflow, create new script version | Rare (only when page changes significantly) |
| Human rejects auto-repair | Manual repair: analyze debug bundle, find correct selector, create new version | Even rarer (human caught a bad repair) |
| 48h after each publish | Evaluate post performance, write improvement notes | Once per post |
| Before drafting next post | Review last 5 evaluations, apply lessons, write draft | Once per post |
| Human requests manual review | Anything the human wants | On demand |
| `social-media-content` skill invoked | Write/edit draft content, save to `posts/pending/` | Per draft |
| `social-marketing` skill invoked | Check history for dupes, review content, invoke script via CLI | Per publish |

Everything else — scheduling, publishing, verifying, retrying, simple selector repair, health checks, log rotation — is pure script execution. No AI, no tokens.

## Implementation Phases

### Phase 1: Dedup + Script State + Diagnosis (1-2 days)

- Add content hash (SHA-256) before every publish, check against `social-media/posts/published/*.json`
- Add draft path dedup check against `social-marketing-history.json`
- Add recent-similarity check (>90% match against last 10 posts)
- Add `script-state.json` to track state per platform (version, failures, approval status)
- Add structured error classification to both scripts (selector-not-found, anti-bot, auth, timeout, unknown)
- Save debug bundles on failure (screenshot + HTML + error log in timestamped folder)
- `verify-only` mode for both scripts: navigates, checks selectors, checks login, does NOT post

### Phase 2: Self-Healing + Versioning (2-3 days)

- Add `repair.py`: reads failure diagnosis, applies repair strategy
- Versioned scripts: repair creates `x-v{N}.py`, never overwrites original. Symlink (`x.py` → `x-v2.py`) points to latest
- `versions.log`: timestamped record of every version change with reason
- Simple selector repair: scan page for closest element, update state file
- Anti-bot backoff: wait + retry with configurable intervals
- Manual approval gate: after repair → retry → verify passes → mark AWAITING_APPROVAL → human runs `repair.py --approve` or `--reject`
- Escalation path: save full debug bundle + mark state as NEEDS_AI
- Wire repair into both scripts' failure paths

### Phase 3: Health Check Scheduler (1 day)

- Add `health-check` job to APScheduler with configurable interval
- Calls both scripts in verify-only mode
- On failure, triggers self-healing
- Logs results to `social-media/logs/health.log`

### Phase 4: Content Improvement Loop (2-3 days)

- Add `post-record.json` template saved after each successful publish
- Add metrics-fetching (platform-dependent: manual for now, API where available)
- Add `evaluate.py`: calls AI with the evaluation prompt, saves result to post record
- Add `draft-with-lessons.py`: feeds last 5 evaluations to AI, generates improved draft
- Wire into scheduler: 48h after publish → evaluate → store lessons

## Success Metrics

| Metric | Target |
|--------|--------|
| Self-healing success rate | >80% of failures auto-repaired |
| Health check catch rate | Catches UI changes before next scheduled post |
| Mean time to repair | <5 min for auto-repair, <1h for AI repair |
| Content improvement | AI evaluation complete for every post within 48h |
| Human touch points | Human only involved at: approve draft, handle auth expiry, handle AI-escalated failures |
