# Notes

## How it works

`monday-scaries owner/repo` → fetch open issues → filter → LLM extracts
facts per issue → code sets severity → sort → table (or `--json`).

## Scope

- One repo per run, github.com only, read-only.
- Open issues only. Pull requests and closed issues are excluded.
- Window: issues updated in the last N days (`--days`, default 7), so
  old issues with new activity are included. `--new-only` keeps only
  issues created in the window.
- Filter order: pull requests → excluded labels (duplicate, invalid,
  wontfix, stale, needs more info) → `--new-only` → `--limit` (default
  50), which caps LLM calls. If nothing is left, print "No open issues"
  and exit 0.

## Not built

- GitHub Enterprise: cut, because the token would be sent to whatever
  host the user typed.
- Reading comments: leads to data cleaning; too big of a feature given the time constraint
- Reading source code, history or trends, a trained classifier: leads to data cleaning; too big of a feature given the time constraint

## With more time

**Evaluation** 
- use maintainers' own severity labels across several
repos as ground truth to tune the impact table and the 👍 threshold.
- hand-label 15–20 issues from one repo; report how often the
tool agrees with me, by severity and by kind. Adjust the system prompt and the severity in which the LLM's judgement can be trusted with a known quantitative metric.

## AI collaboration log