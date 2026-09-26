#!/usr/bin/env python3
"""Create or display a study journal from missed-question tags."""

import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "seed" / "missed.json"
DEFAULT_JOURNAL = PROJECT_ROOT / "journal.json"
DEFAULT_LEADERBOARD = PROJECT_ROOT / "leaderboard.json"
SUPPORTED_DAYS = (5, 7, 10)
PN_SCOPE_LINE = (
    "PN scope: Follow the care plan, reinforce instructions, monitor, "
    "and report changes or concerns."
)
SCOPE_REVIEW_TEMPLATES = [
    "Focused day with one missed topic",
    "Focused day with multiple missed topics",
    "Focused day revisiting an earlier topic",
    "Scope or delegation study action",
    "Comparison study action",
    "Recall-card study action",
    "General takeaway study action",
    "5-day schedule and review days",
    "7-day schedule and review days",
    "10-day schedule and review days",
    "Shared PN scope reminder",
]
REVIEW_REMINDER = [
    "REVIEW PENDING: A nursing-background reviewer must check every plan template",
    "for scope-of-practice wording before sharing this with your study group.",
    "",
]


def review_reminder(journal):
    review = journal.get("scope_review")
    return [] if isinstance(review, dict) and review.get("status") == "complete" else REVIEW_REMINDER.copy()


def load_misses(path):
    try:
        with path.open(encoding="utf-8-sig") as source:
            data = json.load(source)
    except OSError as exc:
        raise ValueError(f"Cannot read seed file {path}: {exc.strerror or exc}") from exc
    except UnicodeError as exc:
        raise ValueError(f"Seed file {path} must be UTF-8 text.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Seed file {path} has invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}."
        ) from exc

    if not isinstance(data, list):
        raise ValueError("Seed file must contain a JSON array of missed-question tags.")
    if not data:
        raise ValueError("Seed file must contain at least one missed-question tag.")

    misses = []
    for number, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Seed item {number} must be a JSON object.")
        if set(item) - {"id", "topic", "client_need", "why_missed"}:
            raise ValueError(
                f"Seed item {number} has unsupported fields; use only id, topic, client_need, and why_missed."
            )
        if "id" in item and (
            isinstance(item["id"], bool)
            or not isinstance(item["id"], (str, int))
            or isinstance(item["id"], str) and not item["id"].strip()
        ):
            raise ValueError(f"Seed item {number} field 'id' must be a nonempty string or integer.")
        cleaned = {}
        for field in ("topic", "client_need", "why_missed"):
            if field not in item:
                raise ValueError(f"Seed item {number} is missing required field '{field}'.")
            value = item[field]
            if not isinstance(value, str):
                raise ValueError(f"Seed item {number} field '{field}' must be text.")
            if not value.strip():
                raise ValueError(f"Seed item {number} field '{field}' cannot be blank.")
            cleaned[field] = " ".join(value.split())
        misses.append(cleaned)
    return misses


def study_action(reason):
    lower = reason.lower()
    if "scope" in lower or "rn-level" in lower or "pn can" in lower or "assign" in lower:
        return (
            "Check your provincial PN guidance and the care plan; identify what to "
            "reinforce, monitor, and report."
        )
    if " vs " in lower or "mixed up" in lower or "confus" in lower:
        return "Make a two-column comparison, then explain the deciding clue from memory."
    if "forgot" in lower or "recall" in lower:
        return "Make a recall card and test it once now and once tomorrow."
    return "Write your own takeaway and the clue you will look for next time."


def normalize_topic(value):
    return " ".join(value.split()).casefold()


def topic_journal_path(topic, plan_days):
    normalized = normalize_topic(topic)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:32].strip("-") or "client-need"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return DEFAULT_JOURNAL.with_name(f"journal-{plan_days}-topic-{slug}-{digest}.json")


def build_journal(misses, plan_days=7, topic=None):
    if plan_days not in SUPPORTED_DAYS:
        raise ValueError(f"Plan length must be one of {SUPPORTED_DAYS} days.")
    focus_days = plan_days - 2
    buckets = [[] for _ in range(focus_days)]
    day_needs = [Counter() for _ in range(focus_days)]
    topic_groups = {}
    for miss in misses:
        key = (normalize_topic(miss["client_need"]), normalize_topic(miss["topic"]))
        topic_groups.setdefault(key, []).append(miss)
    for (need, _), group in topic_groups.items():
        eligible = [
            day for day in range(focus_days) if day_needs[day][need] < 2
        ]
        if not eligible:
            raise ValueError(
                f"Too many distinct topics for client need {group[0]['client_need']!r} "
                f"in a {plan_days}-day plan; each focus day can hold at most two. "
                "Choose a longer --days plan or a smaller seed."
            )
        day = min(eligible, key=lambda index: (len(buckets[index]), day_needs[index][need], index))
        buckets[day].extend(group)
        day_needs[day][need] += 1

    days = []
    for day, bucket in enumerate(buckets, 1):
        tasks = []
        if not bucket:
            revisit = misses[(day - 1) % len(misses)]
            focus = f"Today, give {revisit['topic']} another quick pass."
            tasks.append(f"Revisit {revisit['topic']} [{revisit['client_need']}].")
            tasks.append("Retest your own takeaway, then answer 5 related questions.")
        elif len(bucket) == 1:
            focus = f"Today, get clearer on {bucket[0]['topic']} and the clue you missed."
        else:
            focus = f"Today, turn {len(bucket)} missed topics into clear takeaways."
        for miss in bucket:
            tasks.extend([
                f"{miss['topic']} [{miss['client_need']}]",
                f"Missed because: {miss['why_missed']}",
                f"Action: {study_action(miss['why_missed'])}",
                "Practice: Answer 5 related questions; review results and log new topic tags.",
            ])
        tasks.append(PN_SCOPE_LINE)
        days.append({"day": day, "title": "Focus and practice", "focus": focus, "tasks": tasks,
                     "completed": False, "notes": ""})

    counts = Counter(normalize_topic(miss["client_need"]) for miss in misses)
    priority_key = max(counts, key=counts.get)
    priority = next(miss["client_need"] for miss in misses if normalize_topic(miss["client_need"]) == priority_key)
    priority_topics = []
    seen_topics = set()
    for miss in misses:
        topic_key = normalize_topic(miss["topic"])
        if normalize_topic(miss["client_need"]) == priority_key and topic_key not in seen_topics:
            priority_topics.append(miss["topic"])
            seen_topics.add(topic_key)
        if len(priority_topics) == 2:
            break
    days.append({
        "day": plan_days - 1,
        "title": f"Revisit {priority} ({counts[priority_key]} missed)",
        "focus": f"Today, revisit {priority} and see what you can explain confidently.",
        "tasks": [
            f"Topics: {', '.join(priority_topics)}",
            "Retest the notes for these topics, then answer 10 related questions.",
            "Explain your reasoning in your own words and update unclear takeaways.",
            PN_SCOPE_LINE,
        ],
        "completed": False,
        "notes": "",
    })
    days.append({
        "day": plan_days,
        "title": "Mixed check",
        "focus": "Today, bring the plan's topics together and spot what still needs work.",
        "tasks": [
            f"Build a 20-question mixed set using up to two topics per client need from {len(misses)} recorded misses.",
            "Review results, list remaining weak topics, and carry them into your next plan.",
            PN_SCOPE_LINE,
        ],
        "completed": False,
        "notes": "",
    })
    journal = {
        "version": 1,
        "plan_days": plan_days,
        "missed_count": len(misses),
        "why_missed_counts": dict(Counter(miss["why_missed"] for miss in misses)),
        "study_dates": [],
        "scope_review": {
            "status": "pending",
            "required_before": "sharing with the study group",
            "templates_to_check": SCOPE_REVIEW_TEMPLATES,
        },
        "days": days,
    }
    if topic is not None:
        journal["client_need_filter"] = topic
    return journal


def load_journal(path):
    try:
        with path.open(encoding="utf-8") as source:
            journal = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc

    if not isinstance(journal, dict) or journal.get("version") != 1:
        raise ValueError("Journal needs version 1.")
    count = journal.get("missed_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("Journal needs a positive missed_count.")
    reason_counts = journal.get("why_missed_counts")
    if reason_counts is not None and (
        not isinstance(reason_counts, dict)
        or not reason_counts
        or any(
            not isinstance(reason, str) or not reason.strip()
            or isinstance(reason_count, bool)
            or not isinstance(reason_count, int)
            or reason_count < 1
            for reason, reason_count in reason_counts.items()
        )
        or sum(reason_counts.values()) != count
    ):
        raise ValueError("Journal why_missed_counts must contain positive counts totaling missed_count.")
    study_dates = journal.get("study_dates", [])
    validate_study_dates(study_dates, "Journal study_dates")
    plan_days = journal.get("plan_days", 7)
    if isinstance(plan_days, bool) or plan_days not in SUPPORTED_DAYS:
        raise ValueError(f"Journal plan_days must be one of {SUPPORTED_DAYS}.")
    if "client_need_filter" in journal and (
        not isinstance(journal["client_need_filter"], str)
        or not journal["client_need_filter"].strip()
    ):
        raise ValueError("Journal client_need_filter must be a nonempty string.")
    review = journal.get("scope_review")
    if review is not None:
        if not isinstance(review, dict) or review.get("templates_to_check") != SCOPE_REVIEW_TEMPLATES:
            raise ValueError("Journal scope review needs the full template checklist.")
        if review.get("status") == "complete":
            if not all(
                isinstance(review.get(field), str) and review[field].strip()
                for field in ("reviewer", "reviewed_on", "qualification")
            ):
                raise ValueError("Completed scope review needs reviewer, qualification, and reviewed_on.")
            try:
                reviewed_on = date.fromisoformat(review["reviewed_on"])
            except ValueError as exc:
                raise ValueError("Scope review reviewed_on must be YYYY-MM-DD.") from exc
            if reviewed_on.isoformat() != review["reviewed_on"] or reviewed_on > date.today():
                raise ValueError("Scope review reviewed_on must be YYYY-MM-DD and no later than today.")
        elif review.get("status") != "pending":
            raise ValueError("Journal scope review status must be pending or complete.")
    days = journal.get("days")
    if not isinstance(days, list) or len(days) != plan_days:
        raise ValueError(f"Journal needs exactly {plan_days} days.")
    for number, day in enumerate(days, 1):
        if not isinstance(day, dict) or day.get("day") != number:
            raise ValueError(f"Journal day {number} needs day number {number}.")
        if not isinstance(day.get("title"), str) or not day["title"].strip():
            raise ValueError(f"Journal day {number} needs a title.")
        if "focus" in day and (not isinstance(day["focus"], str) or not day["focus"].strip()):
            raise ValueError(f"Journal day {number} needs a nonempty focus line.")
        tasks = day.get("tasks")
        if not isinstance(tasks, list) or not tasks or any(
            not isinstance(task, str) or not task.strip() for task in tasks
        ):
            raise ValueError(f"Journal day {number} needs nonempty task strings.")
        if not isinstance(day.get("completed"), bool):
            raise ValueError(f"Journal day {number} needs a true/false completed field.")
        if not isinstance(day.get("notes"), str):
            raise ValueError(f"Journal day {number} needs a text notes field.")
    return journal


def validate_study_dates(study_dates, label):
    if not isinstance(study_dates, list) or any(
        not isinstance(value, str) for value in study_dates
    ):
        raise ValueError(f"{label} must contain YYYY-MM-DD strings.")
    if len(study_dates) != len(set(study_dates)):
        raise ValueError(f"{label} must contain unique dates.")
    for value in study_dates:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid study date: {value!r}.") from exc
        if parsed.isoformat() != value or parsed > date.today():
            raise ValueError(f"Study date must be YYYY-MM-DD and no later than today: {value!r}.")


def load_leaderboard(path):
    if not path.exists():
        create_journal(path, {"members": []})
    try:
        with path.open(encoding="utf-8") as source:
            board = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(board, dict) or set(board) != {"members"} or not isinstance(board["members"], list):
        raise ValueError("Leaderboard needs a members array.")
    names = set()
    for number, member in enumerate(board["members"], 1):
        if not isinstance(member, dict) or set(member) != {"name", "study_dates"}:
            raise ValueError(f"Leaderboard member {number} needs only name and study_dates.")
        name = member["name"]
        if not isinstance(name, str) or not name.strip() or "\n" in name or "\r" in name:
            raise ValueError(f"Leaderboard member {number} needs a one-line name.")
        if name.casefold() == "you" or name.casefold() in names:
            raise ValueError(f"Leaderboard member name {name!r} is reserved or repeated.")
        names.add(name.casefold())
        validate_study_dates(member["study_dates"], f"Leaderboard member {number} study_dates")
    return board


def render_leaderboard(journal, board):
    rows = [("You", count_streak(journal.get("study_dates", [])))]
    rows.extend((member["name"], count_streak(member["study_dates"])) for member in board["members"])
    rows.sort(key=lambda row: (-row[1], row[0].casefold()))
    lines = review_reminder(journal) + ["Days studied in a row"]
    lines.extend(f"{rank}. {name}: {days}" for rank, (name, days) in enumerate(rows, 1))
    return "\n".join(lines)


def create_journal(path, journal):
    try:
        with path.open("x", encoding="utf-8") as target:
            json.dump(journal, target, indent=2, ensure_ascii=False)
            target.write("\n")
    except OSError as exc:
        raise ValueError(f"Cannot create {path}: {exc}") from exc


def save_journal(path, journal):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as target:
            temporary = Path(target.name)
            json.dump(journal, target, indent=2, ensure_ascii=False)
            target.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        raise ValueError(f"Cannot update {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def count_streak(study_dates, today=None):
    today = today or date.today()
    recorded = {date.fromisoformat(value) for value in study_dates}
    day = today if today in recorded else today - timedelta(days=1)
    count = 0
    while day in recorded:
        count += 1
        day -= timedelta(days=1)
    return count


def reason_breakdown(journal):
    if "why_missed_counts" in journal:
        return journal["why_missed_counts"]
    # Older journals already contain each reason once in their focused-day tasks.
    return dict(Counter(
        task.removeprefix("Missed because: ")
        for day in journal["days"]
        for task in day["tasks"]
        if task.startswith("Missed because: ")
    ))


def render_journal(journal):
    missed_count = journal["missed_count"]
    missed_word = "question" if missed_count == 1 else "questions"
    lines = review_reminder(journal) + [
        "Today's focus",
        f"Based on {missed_count} missed {missed_word}. "
        f"Days studied in a row: {count_streak(journal.get('study_dates', []))}.",
        "",
    ]
    reasons = reason_breakdown(journal)
    if reasons:
        lines.append("Why I missed them:")
        lines.extend(
            f"  • {amount} × {reason}"
            for reason, amount in sorted(reasons.items(), key=lambda item: (-item[1], item[0].casefold()))
        )
        lines.append("")
    for day in journal["days"]:
        status = "complete" if day["completed"] else "pending"
        lines.append(f"Day {day['day']} — {day['title']} ({status})")
        if "focus" in day:
            lines.append(f"  Focus: {day['focus']}")
        lines.extend(f"  • {task}" for task in day["tasks"])
        if day["notes"].strip():
            lines.append(f"  Notes: {day['notes']}")
        lines.append("")
    return "\n".join(lines)


def plan_as_json(journal):
    output = {"header": "Today's focus", **journal}
    output["days_studied_in_a_row"] = count_streak(journal.get("study_dates", []))
    output["why_missed_counts"] = reason_breakdown(journal)
    output["scope_review"] = journal.get("scope_review") or {
        "status": "pending",
        "required_before": "sharing with the study group",
        "templates_to_check": SCOPE_REVIEW_TEMPLATES,
    }
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="missed-question JSON used when creating a journal (default: seed/missed.json)")
    parser.add_argument("--days", type=int, choices=SUPPORTED_DAYS,
                        help="plan length: 5, 7, or 10 days (default: 7)")
    parser.add_argument("--topic", type=str,
                        help="build a plan for one exact client-need category")
    parser.add_argument("--journal", type=Path,
                        help="journal JSON to create or display (default: journal.json or journal-N.json)")
    parser.add_argument("--mark-studied", action="store_true",
                        help="record today's local date in the selected journal")
    parser.add_argument("--leaderboard", action="store_true",
                        help="show local study-day ranking instead of the plan")
    parser.add_argument("--leaderboard-file", type=Path, default=DEFAULT_LEADERBOARD,
                        help="editable group dates JSON (default: leaderboard.json)")
    parser.add_argument("--json", action="store_true",
                        help="print the plan as JSON instead of text")
    args = parser.parse_args(argv)
    if args.json and args.leaderboard:
        parser.error("--json is for plans and cannot be combined with --leaderboard.")
    if args.topic is not None and not args.topic.strip():
        parser.error("--topic needs a nonempty client-need category.")
    requested_days = args.days or 7
    journal_path = args.journal or (
        topic_journal_path(args.topic, requested_days) if args.topic is not None
        else DEFAULT_JOURNAL if requested_days == 7
        else DEFAULT_JOURNAL.with_name(f"journal-{requested_days}.json")
    )
    try:
        if journal_path.exists():
            journal = load_journal(journal_path)
            if args.days is not None and journal.get("plan_days", 7) != args.days:
                raise ValueError(
                    f"{journal_path} is a {journal.get('plan_days', 7)}-day journal; "
                    f"choose a different --journal path for {args.days} days."
                )
            if args.topic is not None and normalize_topic(journal.get("client_need_filter", "")) != normalize_topic(args.topic):
                raise ValueError(
                    f"{journal_path} is not a plan for client need {args.topic!r}; "
                    "choose a different --journal path."
                )
        else:
            misses = load_misses(args.input)
            if args.topic is not None:
                selected = normalize_topic(args.topic)
                misses = [miss for miss in misses if normalize_topic(miss["client_need"]) == selected]
                if not misses:
                    raise ValueError(f"No misses match client need {args.topic!r}.")
            journal = build_journal(
                misses, requested_days,
                misses[0]["client_need"] if args.topic is not None else None,
            )
            if args.mark_studied:
                journal["study_dates"].append(date.today().isoformat())
            create_journal(journal_path, journal)
        if args.mark_studied and date.today().isoformat() not in journal.get("study_dates", []):
            journal.setdefault("study_dates", []).append(date.today().isoformat())
            save_journal(journal_path, journal)
        board = load_leaderboard(args.leaderboard_file) if args.leaderboard else None
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(plan_as_json(journal), indent=2, ensure_ascii=False))
    else:
        print(render_leaderboard(journal, board) if args.leaderboard else render_journal(journal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
