# Notes

## How it works

- `monday-scaries owner/repo` → fetch open issues → filter → LLM extracts
  facts per issue → code sets severity → sort → color-coded report or
  `--json`.
- The LLM never decides severity. It reports facts (kind, area, impact,
  workaround, scope, regression, summary); code turns them into a level.
  This keeps the policy testable and means an injected issue cannot set
  its own severity.

## How I scoped it

- One repo per run, github.com only, read-only. Open issues only; pull
  requests and closed issues are dropped.
- "New" baseline: issues active in the last N days (`--days`, default 7),
  so old issues with fresh activity are included. `--new-only` keeps only
  issues created in the window. Chosen over "since the last release"
  because it is predictable for a weekly triage.
- Filter order: pull requests → closing labels (duplicate, invalid,
  wontfix, stale, needs more info) → `--new-only` → `--limit` (default
  50), which caps LLM calls and cost.
- One issue per LLM call, temperature 0 where the provider allows it
  (current Claude models reject `temperature`). One call per issue means
  an injected issue only affects itself and a failure loses one issue.
- Issue text is wrapped in tags, HTML-escaped, and marked as data. LLM
  output is validated against allowed values; invalid output is retried
  once, then the issue is "unrated".
- Severity policy (a starting point, not validated):
  - only bugs get a severity; a maintainer severity/priority label wins;
  - otherwise impact sets the level: security/data loss → critical;
    wrong results/crash/broken feature → high; performance → medium;
    cosmetic → low; unclear → `?` (needs review);
  - regression and 10+ 👍 move up, workaround and edge case move down,
    at most one level in total; critical never moves down;
  - an unreadable issue with a bug label goes to `?`, not the bottom:
    missing a critical bug costs more than a false alarm.
- Keys come only from environment variables or `~/.monday_scaries/.env`;
  a project `.env` is never read, and no error or log prints a key.

## What I didn't build

- GitHub Enterprise: cut, because the token would be sent to whatever
  host the user typed.
- Reading comments: more API calls (60/hour without a token) and more
  text to clean; cut for time.
- Reading source code, history or trends, a trained classifier.
- Hand-labeled evaluation of agreement with my own labels (see below).
- Live checks for Groq, OpenRouter, and custom endpoints: mocked tests
  only. Claude, OpenAI, and Gemini were run against the real APIs.

## With more time

- Evaluate the policy: use maintainers' own severity labels across
  several repos as ground truth, plus 15–20 issues I label by hand;
  report agreement by severity and kind, then tune the impact table,
  the 👍 threshold, and the prompt against that number.
- Measure run-to-run stability. Two Claude runs a few minutes apart
  rated pandas #71978 differently: medium with "workaround" in one, high
  without it in the other, and its category flipped between other and
  input-output. Claude does not accept temperature 0, so run each issue
  2-3 times, use the majority answer, and flag disagreements as `?`.
- Finer categories: almost everything lands in "input-output" for data
  libraries. Show a component taken from the repo's own labels (pandas
  already uses `IO SQL`, `Groupby`, `Arrow`) next to the required
  category.

## AI collaboration log

- Overrode: the AI's first design had the LLM pick severity. I changed it
  to fact extraction plus severity in code, so the policy is testable and
  an injected issue cannot set its own severity.
- Overrode: I asked for tests with every feature, one file and one
  feature at a time, each run for real with `uv run` before committing;
  the AI had planned a single test step at the end. I also removed the
  AI attribution lines it added to commit messages.
- Double-checked: a wrong key never shows up in errors; the Claude and
  Gemini 400s, which turned out to be key problems, not code; my email
  and API keys kept out of git history before every push.
