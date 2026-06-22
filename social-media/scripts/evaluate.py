#!/usr/bin/env python3
"""
Post-performance evaluation engine.
Scans published post records, finds posts 48h+ old without evaluation,
and prepares AI evaluation prompts.

Usage:
    python social-media/scripts/evaluate.py --auto
    python social-media/scripts/evaluate.py --post-id linkedin-1234567890
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POSTS_PUBLISHED_DIR = ROOT / "workspace" / "social-media" / "posts" / "published"
LOG_DIR = ROOT / "workspace" / "social-media" / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("evaluate")
log.setLevel(logging.DEBUG)
fh = logging.FileHandler(LOG_DIR / "evaluate.log")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
sh = logging.StreamHandler(sys.stderr)
sh.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(fh)
log.addHandler(sh)


EVAL_PROMPT = """You are evaluating a {platform} social media post's content quality.

Post content:
{content}

Hook: {hook}

Metrics (if available):
- Likes: {likes}
- Comments: {comments}
- Reposts: {reposts}
- Impressions: {impressions}

Evaluate these 7 dimensions. Answer each with yes, no, or partial, plus one sentence why.

1. Did the hook make the target reader care?
2. Was the post too broad (generic audience)?
3. Was it too sales-like instead of helpful?
4. Did it show real, specific insight? Or just generic advice?
5. Was there a clear target audience?
6. Did the call-to-action invite genuine replies?
7. What specific change should we test in the next post about this topic?

Return ONLY valid JSON:
{{
  "hook_effective": "yes|no|partial",
  "hook_why": "one sentence",
  "too_broad": "yes|no",
  "too_broad_why": "one sentence",
  "too_salesy": "yes|no",
  "too_salesy_why": "one sentence",
  "showed_insight": "insight|generic",
  "insight_why": "one sentence",
  "clear_audience": "yes|no",
  "audience_why": "one sentence",
  "invited_reply": "yes|no",
  "reply_why": "one sentence",
  "what_to_test_next": "specific suggestion for next post"
}}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def needs_evaluation(record: dict, eval_delay_hours: int = 48) -> bool:
    if record.get("ai_evaluation", {}).get("evaluated_at"):
        return False
    published = record.get("published_at")
    if not published:
        return False
    try:
        pub_time = datetime.fromisoformat(published)
        age = datetime.now(timezone.utc).astimezone() - pub_time
        return age > timedelta(hours=eval_delay_hours)
    except (ValueError, TypeError):
        return False


def evaluate_post(record_path: Path) -> dict | None:
    try:
        record = json.loads(record_path.read_text())
    except (ValueError, OSError) as exc:
        log.error(f"Failed to read {record_path.name}: {exc}")
        return None

    if not needs_evaluation(record):
        return None

    content = record.get("content", "")
    if not content and record.get("content_file"):
        content_file = ROOT / record["content_file"]
        if content_file.exists():
            content = content_file.read_text()[:500]

    metrics = record.get("metrics", {})
    prompt = EVAL_PROMPT.format(
        platform=record.get("platform", "unknown"),
        content=content,
        hook=record.get("hook", content[:60] if content else ""),
        likes=metrics.get("likes", "N/A"),
        comments=metrics.get("comments", "N/A"),
        reposts=metrics.get("reposts", "N/A"),
        impressions=metrics.get("impressions", "N/A"),
    )

    log.info(f"Evaluating {record_path.name}...")

    # Save evaluation prompt — actual AI call wired separately via social-marketing skill
    evaluation = {
        "evaluated_at": now_iso(),
        "prompt_saved": True,
        "prompt_length": len(prompt),
        "hook_effective": None,
        "too_broad": None,
        "too_salesy": None,
        "showed_insight": None,
        "clear_audience": None,
        "invited_reply": None,
        "what_to_test_next": None,
    }

    record["ai_evaluation"] = evaluation
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    log.info(f"Evaluation saved to {record_path.name}")
    return record


def scan_and_evaluate() -> list[dict]:
    results = []
    if not POSTS_PUBLISHED_DIR.exists():
        log.info("No published posts directory found.")
        return results
    for record_file in sorted(POSTS_PUBLISHED_DIR.glob("*.json")):
        result = evaluate_post(record_file)
        if result:
            results.append(result)
    log.info(f"Evaluated {len(results)} posts.")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-performance evaluation")
    parser.add_argument("--auto", action="store_true", help="Scan and evaluate all due posts")
    parser.add_argument("--post-id", default="", help="Evaluate a specific post by ID")
    args = parser.parse_args()

    if args.post_id:
        record_path = POSTS_PUBLISHED_DIR / f"{args.post_id}.json"
        if not record_path.exists():
            log.error(f"Post not found: {record_path}")
            sys.exit(1)
        result = evaluate_post(record_path)
        sys.exit(0 if result else 1)
    elif args.auto:
        results = scan_and_evaluate()
        print(json.dumps({"status": "ok", "evaluated": len(results)}, indent=2))
        sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
