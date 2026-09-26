"""Behavior checks for the local JSON study journal."""

import ast
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from pn_remediation import (
    PN_SCOPE_LINE, build_journal, count_streak, load_journal,
    render_journal, study_action,
)


SCRIPT = Path(__file__).resolve().parents[1] / "remediation.py"
TEMPLATE_SOURCE = Path(__file__).resolve().parents[1] / "pn_remediation" / "app.py"
RN_SCOPE_PATTERNS = {
    "independent assessment": re.compile(
        r"\b(?:assess\w*\W+independent\w*|independent\w*\W+assess\w*)\b", re.I
    ),
    "diagnosis": re.compile(r"\bdiagnos(?:e|es|ed|ing|is|tic)\b", re.I),
    "initiating treatment": re.compile(
        r"\binitiat\w*(?:\W+\w+){0,2}\W+treatment\b", re.I
    ),
    "prescribing": re.compile(r"\bprescrib(?:e|es|ed|ing)\b", re.I),
}


def sample_misses():
    return [
        {"topic": "topic A", "client_need": "safety", "why_missed": "mixed up two concepts"},
        {"topic": "topic B", "client_need": "care", "why_missed": "forgot detail"},
        {"topic": "topic C", "client_need": "safety", "why_missed": "unsure what PN can assign"},
        {"topic": "topic D", "client_need": "care", "why_missed": "missed a clue"},
    ]


class RemediationTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True, text=True, check=False,
        )

    def test_all_plan_lengths_keep_topics_scope_and_review_gate(self):
        for length in (5, 7, 10):
            with self.subTest(days=length):
                journal = build_journal(sample_misses(), length)
                self.assertEqual(len(journal["days"]), length)
                self.assertEqual(journal["scope_review"]["status"], "pending")
                for day in journal["days"]:
                    self.assertIn(PN_SCOPE_LINE, day["tasks"])
                focus_text = " ".join(
                    task for day in journal["days"][:-2] for task in day["tasks"]
                )
                for miss in sample_misses():
                    self.assertIn(miss["topic"], focus_text)
                self.assertEqual(journal["days"][-1]["title"], "Mixed check")

    def test_no_day_exceeds_two_topics_per_client_need(self):
        for length in (5, 7, 10):
            with self.subTest(days=length):
                misses = [
                    {"topic": f"safety-{number}", "client_need": "safety", "why_missed": "own note"}
                    for number in range(2 * (length - 2))
                ] + [
                    {"topic": f"care-{number}", "client_need": "care", "why_missed": "own note"}
                    for number in range(2 * (length - 2))
                ]
                journal = build_journal(misses, length)
                for day in journal["days"][:-2]:
                    for need in ("safety", "care"):
                        assigned = [
                            task for task in day["tasks"]
                            if task.startswith(f"{need}-") and task.endswith(f" [{need}]")
                        ]
                        self.assertLessEqual(len(assigned), 2)
                assigned_tasks = [
                    task for day in journal["days"][:-2] for task in day["tasks"]
                ]
                for miss in misses:
                    self.assertEqual(
                        assigned_tasks.count(f"{miss['topic']} [{miss['client_need']}]"), 1
                    )
                review_topics = next(
                    task for task in journal["days"][-2]["tasks"] if task.startswith("Topics: ")
                )
                self.assertLessEqual(len(review_topics.removeprefix("Topics: ").split(", ")), 2)
                self.assertIn("up to two topics per client need", journal["days"][-1]["tasks"][0])

    def test_repeated_topic_fits_and_infeasible_seed_creates_no_journal(self):
        misses = [
            {"topic": "safety-0", "client_need": "safety", "why_missed": "own note"}
            for _ in range(4)
        ] + [
            {"topic": f"safety-{number}", "client_need": "safety", "why_missed": "own note"}
            for number in range(1, 6)
        ]
        journal = build_journal(misses, 5)
        for day in journal["days"][:-2]:
            topics = {
                task for task in day["tasks"] if task.startswith("safety-") and task.endswith(" [safety]")
            }
            self.assertLessEqual(len(topics), 2)
        self.assertEqual(journal["missed_count"], len(misses))

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missed.json"
            destination = Path(folder) / "journal.json"
            source.write_text(json.dumps(misses + [
                {"topic": "safety-6", "client_need": "safety", "why_missed": "own note"}
            ]), encoding="utf-8")
            result = self.run_cli("--days", "5", "--input", source, "--journal", destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Too many distinct topics", result.stderr)
            self.assertFalse(destination.exists())

    def test_every_plan_template_avoids_rn_scope_wording(self):
        examples = (
            "assess independently", "independently assess", "diagnose",
            "initiate treatment", "prescribe",
        )
        self.assertTrue(all(
            any(pattern.search(example) for pattern in RN_SCOPE_PATTERNS.values())
            for example in examples
        ))

        # Static template literals catch wording in a branch a sample plan might miss.
        tree = ast.parse(TEMPLATE_SOURCE.read_text(encoding="utf-8"))
        template_nodes = [
            node for node in tree.body
            if (
                isinstance(node, ast.FunctionDef)
                and node.name in {"study_action", "build_journal", "render_journal"}
            ) or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "PN_SCOPE_LINE"
                    for target in node.targets
                )
            )
        ]
        texts = [
            (f"template literal {number}", child.value)
            for number, node in enumerate(template_nodes, 1)
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        ]

        # These plans exercise every current action branch and each day layout.
        self.assertEqual(len({study_action(m["why_missed"]) for m in sample_misses()}), 4)
        for length in (5, 7, 10):
            texts.append((f"rendered {length}-day plan", render_journal(
                build_journal(sample_misses(), length)
            )))

        for location, text in texts:
            for wording, pattern in RN_SCOPE_PATTERNS.items():
                with self.subTest(location=location, wording=wording):
                    self.assertNotRegex(text, pattern)

    def test_reason_breakdown_counts_repeats_and_supports_existing_journals(self):
        misses = sample_misses()
        misses[2]["why_missed"] = misses[0]["why_missed"]
        journal = build_journal(misses)
        self.assertEqual(journal["why_missed_counts"]["mixed up two concepts"], 2)
        rendered = render_journal(journal)
        self.assertLess(rendered.index("Why I missed them:"), rendered.index("Day 1"))
        self.assertIn("  • 2 × mixed up two concepts", rendered)
        self.assertIn("  • 1 × forgot detail", rendered)

        del journal["why_missed_counts"]
        self.assertIn("  • 2 × mixed up two concepts", render_journal(journal))

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            path.write_text(json.dumps(journal), encoding="utf-8")
            result = self.run_cli("--journal", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Why I missed them:", result.stdout)

            journal["why_missed_counts"] = {"mixed up two concepts": 3}
            path.write_text(json.dumps(journal), encoding="utf-8")
            invalid = self.run_cli("--journal", path)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("why_missed_counts", invalid.stderr)

    def test_json_plan_output_is_parseable_and_keeps_review_status(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missed.json"
            destination = Path(folder) / "journal.json"
            source.write_text(json.dumps(sample_misses()), encoding="utf-8")
            result = self.run_cli(
                "--input", source, "--journal", destination, "--days", "5", "--json"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["header"], "Today's focus")
            self.assertEqual(plan["plan_days"], 5)
            self.assertEqual(len(plan["days"]), 5)
            self.assertEqual(plan["missed_count"], 4)
            self.assertEqual(plan["days_studied_in_a_row"], 0)
            self.assertEqual(plan["why_missed_counts"]["forgot detail"], 1)
            self.assertEqual(plan["scope_review"]["status"], "pending")
            self.assertNotIn("REVIEW PENDING:", result.stdout)

            saved = destination.read_bytes()
            again = self.run_cli("--journal", destination, "--json")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(json.loads(again.stdout), plan)
            self.assertEqual(destination.read_bytes(), saved)

            conflict = self.run_cli("--journal", destination, "--json", "--leaderboard")
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("cannot be combined", conflict.stderr)

    def test_completed_scope_review_hides_reminder_and_keeps_attribution(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            journal = build_journal(sample_misses())
            journal["scope_review"] = {
                "status": "complete",
                "required_before": "sharing with the study group",
                "templates_to_check": journal["scope_review"]["templates_to_check"],
                "reviewer": "Example reviewer",
                "qualification": "Nursing background confirmed by owner",
                "reviewed_on": date.today().isoformat(),
            }
            path.write_text(json.dumps(journal), encoding="utf-8")
            loaded = load_journal(path)
            self.assertNotIn("REVIEW PENDING:", render_journal(loaded))
            text_result = self.run_cli("--journal", path)
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            self.assertTrue(text_result.stdout.startswith("Today's focus"))
            board_result = self.run_cli("--journal", path, "--leaderboard",
                                        "--leaderboard-file", Path(folder) / "board.json")
            self.assertEqual(board_result.returncode, 0, board_result.stderr)
            self.assertTrue(board_result.stdout.startswith("Days studied in a row"))
            json_result = self.run_cli("--journal", path, "--json")
            self.assertEqual(json_result.returncode, 0, json_result.stderr)
            self.assertEqual(json.loads(json_result.stdout)["scope_review"], journal["scope_review"])

            journal["scope_review"].pop("qualification")
            path.write_text(json.dumps(journal), encoding="utf-8")
            invalid = self.run_cli("--journal", path)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("needs reviewer, qualification, and reviewed_on", invalid.stderr)

    def test_marking_a_day_preserves_edits_and_does_not_duplicate_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            source = Path(folder) / "missed.json"
            source.write_text(json.dumps(sample_misses()), encoding="utf-8")
            created = self.run_cli("--days", "5", "--input", source, "--journal", path)
            self.assertEqual(created.returncode, 0, created.stderr)

            journal = load_journal(path)
            journal["days"][0]["completed"] = True
            journal["days"][0]["notes"] = "My own takeaway."
            path.write_text(json.dumps(journal, indent=2), encoding="utf-8")

            first = self.run_cli("--journal", path, "--mark-studied")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("Days studied in a row: 1.", first.stdout)
            self.assertTrue(first.stdout.startswith("REVIEW PENDING:"))
            first_bytes = path.read_bytes()
            second = self.run_cli("--journal", path, "--mark-studied")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(path.read_bytes(), first_bytes)

            saved = load_journal(path)
            self.assertEqual(saved["study_dates"], [date.today().isoformat()])
            self.assertTrue(saved["days"][0]["completed"])
            self.assertEqual(saved["days"][0]["notes"], "My own takeaway.")

            mismatch = self.run_cli("--days", "10", "--journal", path)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertEqual(path.read_bytes(), first_bytes)

    def test_topic_filters_exact_client_need_and_preserves_output_format(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missed.json"
            source.write_text(json.dumps(sample_misses()), encoding="utf-8")
            for length in (5, 7, 10):
                with self.subTest(days=length):
                    path = Path(folder) / f"journal-{length}.json"
                    result = self.run_cli(
                        "--input", source, "--journal", path, "--days", length,
                        "--topic", "  SAFETY  ",
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(result.stdout.startswith("REVIEW PENDING:"))
                    self.assertIn("Today's focus\nBased on 2 missed questions.", result.stdout)
                    self.assertIn("Days studied in a row: 0.", result.stdout)
                    self.assertIn("topic A", result.stdout)
                    self.assertIn("topic C", result.stdout)
                    self.assertNotIn("topic B", result.stdout)
                    self.assertNotIn("topic D", result.stdout)
                    journal = load_journal(path)
                    self.assertEqual(journal["client_need_filter"], "safety")
                    self.assertEqual(journal["missed_count"], 2)
                    self.assertEqual(len(journal["days"]), length)

                    again = self.run_cli("--journal", path, "--topic", "safety")
                    self.assertEqual(again.returncode, 0, again.stderr)
                    self.assertEqual(result.stdout, again.stdout)

                    wrong = self.run_cli("--journal", path, "--topic", "care")
                    self.assertNotEqual(wrong.returncode, 0)
                    self.assertIn("not a plan", wrong.stderr)

            missing_path = Path(folder) / "missing.json"
            missing = self.run_cli(
                "--input", source, "--journal", missing_path, "--topic", "unknown"
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("No misses match", missing.stderr)
            self.assertFalse(missing_path.exists())

    def test_streak_uses_calendar_dates(self):
        today = date(2026, 9, 25)
        self.assertEqual(count_streak([], today), 0)
        self.assertEqual(count_streak(["2026-09-23", "2026-09-24"], today), 2)
        self.assertEqual(count_streak(["2026-09-23", "2026-09-25"], today), 1)
        self.assertEqual(count_streak(["2026-09-21", "2026-09-23"], today), 0)

    def test_leaderboard_ranks_local_dates_without_plan_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            board_path = Path(folder) / "leaderboard.json"
            journal = build_journal(sample_misses())
            journal["days"][0]["notes"] = "Private study note"
            journal["study_dates"] = [date.today().isoformat()]
            path.write_text(json.dumps(journal), encoding="utf-8")
            first = self.run_cli("--journal", path, "--leaderboard", "--leaderboard-file", board_path)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(board_path.read_text())["members"], [])
            self.assertIn("1. You: 1", first.stdout)
            self.assertNotIn("Private study note", first.stdout)
            self.assertNotIn("insulin", first.stdout)

            yesterday = date.fromordinal(date.today().toordinal() - 1).isoformat()
            board_path.write_text(json.dumps({"members": [
                {"name": "Alex", "study_dates": [yesterday, date.today().isoformat()]},
                {"name": "Sam", "study_dates": []},
            ]}), encoding="utf-8")
            ranked = self.run_cli("--journal", path, "--leaderboard", "--leaderboard-file", board_path)
            self.assertEqual(ranked.returncode, 0, ranked.stderr)
            self.assertLess(ranked.stdout.index("1. Alex: 2"), ranked.stdout.index("2. You: 1"))
            self.assertLess(ranked.stdout.index("2. You: 1"), ranked.stdout.index("3. Sam: 0"))
            self.assertTrue(ranked.stdout.startswith("REVIEW PENDING:"))

            board_path.write_text(json.dumps({"members": [
                {"name": "Alex", "study_dates": [date.today().isoformat()]},
                {"name": "Alex", "study_dates": []},
            ]}), encoding="utf-8")
            invalid = self.run_cli("--journal", path, "--leaderboard", "--leaderboard-file", board_path)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("repeated", invalid.stderr)

    def test_rejects_extra_input_fields_without_creating_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missed.json"
            destination = Path(folder) / "journal.json"
            item = sample_misses()[0] | {"question_text": "Disallowed field"}
            source.write_text(json.dumps([item]), encoding="utf-8")
            result = self.run_cli("--input", source, "--journal", destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported fields", result.stderr)
            self.assertFalse(destination.exists())

    def test_seed_validation_reports_file_item_and_field_errors(self):
        valid = sample_misses()[0]
        cases = [
            ("invalid JSON", b'[\n  {"topic":', "invalid JSON at line 2, column"),
            ("invalid UTF-8", b"\xff", "must be UTF-8 text"),
            ("wrong root", b"{}", "must contain a JSON array"),
            ("empty array", b"[]", "at least one missed-question tag"),
            ("wrong item", b"[null]", "Seed item 1 must be a JSON object"),
            ("missing field", json.dumps([{"topic": "tag", "why_missed": "my note"}]).encode(),
             "Seed item 1 is missing required field 'client_need'"),
            ("wrong field type", json.dumps([valid | {"client_need": 42}]).encode(),
             "Seed item 1 field 'client_need' must be text"),
            ("blank field", json.dumps([valid | {"why_missed": "  "}]).encode(),
             "Seed item 1 field 'why_missed' cannot be blank"),
            ("bad id", json.dumps([valid | {"id": False}]).encode(),
             "Seed item 1 field 'id' must be a nonempty string or integer"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missed.json"
            destination = Path(folder) / "journal.json"
            missing = self.run_cli("--input", source, "--journal", destination)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("Cannot read seed file", missing.stderr)
            self.assertFalse(destination.exists())

            for label, content, expected in cases:
                with self.subTest(case=label):
                    source.write_bytes(content)
                    result = self.run_cli("--input", source, "--journal", destination)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertFalse(destination.exists())

            source.write_text(json.dumps([valid | {"topic": "  topic   A  "}]), encoding="utf-8-sig")
            accepted = self.run_cli("--input", source, "--journal", destination)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn("topic A", accepted.stdout)


if __name__ == "__main__":
    unittest.main()
