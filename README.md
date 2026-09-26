# Remediation plans

The planner lives in the local `pn_remediation` package. Run it through the
existing script with Python 3 and no dependencies:

```sh
cp seed/missed.example.json seed/missed.json
python3 remediation.py
```

Replace the example tags in `seed/missed.json` with your own before making a
real plan. Your seed, journals, and leaderboard are ignored by Git so they
stay on your device. Do not commit or share those files.

Use `python3 remediation.py --json` to print the plan as JSON. It includes the
saved plan, the calculated days-studied counter, the `why_missed` breakdown,
and the saved scope-review status. The default remains the text plan.
`--json` cannot be combined with `--leaderboard`.

The first run reads `seed/missed.json`, creates an indented UTF-8 `journal.json`,
and prints the plan. Edit each day's `notes` text and `completed` true/false
value in any text editor. Each day also has an editable one-line `focus`.
Later runs read the existing journal and preserve your edits. To start a
separate journal from another local seed file:

```sh
python3 remediation.py --input path/to/missed.json --journal path/to/new-journal.json
```

The plan summary includes a count of each `why_missed` reason, with repeated
reasons grouped together. New journals save these counts in JSON; older
journals show the breakdown from their existing day tasks.

Use `--days 5` or `--days 10` for a shorter or longer plan. Seven days is the
default. These options use `journal-5.json` and `journal-10.json` by default,
so the existing `journal.json` stays intact. You can choose a different file
with `--journal`; an existing journal with a different length is rejected.

Use `--topic` to build a plan from one client-need category in the seed file:

```sh
python3 remediation.py --topic "safety and infection control"
```

The match uses the full `client_need` label, ignoring case and extra spaces.
Focused plans use a separate JSON journal by default; `--days` and `--journal`
work with them too. A category with no matches gives an error and creates no
journal. Existing journals keep their saved plan rather than being rebuilt.

To record a study day, run `python3 remediation.py --mark-studied`. The CLI
adds today's local date to `study_dates` in the selected JSON journal. You can
also edit that list in a text editor using `YYYY-MM-DD` dates. Recording the
same date twice has no effect. The summary shows only one motivation counter:
days studied in a row. It counts consecutive calendar dates through today or
yesterday; after a longer gap, it shows zero.

For a local study-group ranking, run `python3 remediation.py --leaderboard`.
The CLI creates `leaderboard.json` with an empty `members` list. Add
members in any text editor, using only a name and study dates:

```json
{
  "members": [
    {"name": "Study partner", "study_dates": ["2026-09-24", "2026-09-25"]}
  ]
}
```

The ranking includes your journal as `You`, sorts by days studied in a row,
and prints no journal notes or plan tasks. Dates must be unique, use
`YYYY-MM-DD`, and cannot be in the future. Use `--leaderboard-file PATH` for
another JSON file. Group dates are entered manually; nothing is sent online.

The input must be a nonempty JSON array. Each object needs nonempty `topic`,
`client_need`, and `why_missed` strings. An `id` field is optional and must be
a nonempty string or integer. Invalid seed files report the file, item number,
and field where possible; malformed JSON reports its line and column. No
journal is created when the seed is invalid. The first
`N - 2` days distribute every missed question across focused practice and
targeted revisits. The next day revisits the most frequent client need; the
last day is a mixed check. Each day uses at most two distinct topics from any
one client need. If the focused days cannot fit all topics under that limit,
the CLI asks for a longer plan or a smaller seed instead of omitting topics.
Existing journals keep their saved schedules. Scope and delegation prompts
direct you to your
provincial guidance. Each day repeats
the PN scope lens: follow the care plan, reinforce instructions, monitor,
and report changes or concerns.

Only enter topic tags, client-need categories, and your own notes. Never put
question stems, answer choices, or paraphrases of paid qbank questions from
UWorld, Archer, Kaplan, or any other provider into the seed or journal. The
CLI reads only the three tag fields above and writes generic study prompts;
it cannot determine whether text you manually enter came from a qbank.

All saved data stays in plain JSON files. The CLI makes no network requests.

## Review before sharing

Have someone with nursing background check the scope-of-practice wording in
every plan template before showing a plan to your study group. Each journal
stores its review status and the full template checklist in `scope_review`.
The example seed has not received a nursing-background review. While a
journal's status is `pending`, the CLI prints a reminder on plan and
leaderboard runs. Once the reviewer and their nursing background are confirmed,
record `status: "complete"`, `reviewer`, `qualification`, and `reviewed_on`
(YYYY-MM-DD) in the journal's `scope_review` object. The reminder then clears
for that journal. A new journal starts pending until its review is recorded.

Run the local behavior checks with `python3 -m unittest discover -s tests`.
They scan plan templates for independent assessment, diagnosis, treatment
initiation, and prescribing language. These checks do not replace the
nursing-background review.
