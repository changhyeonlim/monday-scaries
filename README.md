# Monday Scaries

A command-line tool that triages the open issues of a public GitHub
repository with an LLM. It is read-only: it never writes to GitHub.

For each recently active issue, the LLM reports **facts** (kind, area,
impact, workaround, scope, regression, one-sentence summary). Code then
sets the **severity** from those facts and from the maintainers' own
labels, sorts the issues, and prints a color-coded report or JSON.

Design notes, scope, and what was left out are in [NOTES.md](NOTES.md).

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). uv
  installs a matching Python (3.10+) by itself.
- An API key for one LLM provider (Claude is the default).
- Optional: a `GITHUB_TOKEN` for 5,000 GitHub requests per hour instead
  of 60. Not needed for public repositories.

## Install

From GitHub, on a fresh machine:

```bash
uv tool install --compile-bytecode git+https://github.com/changhyeonlim/monday-scaries
```

Or from a local checkout:

```bash
git clone https://github.com/changhyeonlim/monday-scaries
cd monday-scaries
uv tool install --compile-bytecode .
```

Either way, `monday-scaries` is now a command on your PATH. If your shell
cannot find it, run `uv tool update-shell` and open a new terminal.

`--compile-bytecode` makes the first run fast; without it, Python
compiles the LLM SDKs on first use.

## Set up your API key

Keys are read from **environment variables**, or from one file in your
**home folder**. A `.env` file in the project folder is never read, so a
key cannot end up in git.

**Option A: environment variable** (this terminal only)

```powershell
# PowerShell: copy the key first; it never appears on screen or in history
$env:ANTHROPIC_API_KEY = (Get-Clipboard).Trim()
```

```bash
# bash / zsh
export ANTHROPIC_API_KEY="..."
```

**Option B: key file** (set once, works in every terminal)

Copy [.env.example](.env.example) to `~/.monday_scaries/.env` and fill
in the keys you have:

```powershell
# PowerShell
New-Item -ItemType Directory -Force "$HOME\.monday_scaries" | Out-Null
Copy-Item .env.example "$HOME\.monday_scaries\.env"
notepad "$HOME\.monday_scaries\.env"
```

```bash
# macOS / Linux
mkdir -p ~/.monday_scaries
cp .env.example ~/.monday_scaries/.env
chmod 600 ~/.monday_scaries/.env
```

A variable set in your terminal always wins over the file. Only the names
listed in `.env.example` are read from it.

**Check what is set** (names and sources only, never values):

```bash
monday-scaries --show-keys
```

| Provider | `--provider` | Key variable | Default model |
|---|---|---|---|
| Anthropic Claude (default) | `claude` | `ANTHROPIC_API_KEY` | `claude-haiku-5-5` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | `gemini-flash-latest` |
| Groq | `groq` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` |
| Self-hosted (e.g. Ollama) | `custom` | `MONDAY_SCARIES_CUSTOM_BASE_URL` (key optional) | `llama3.1` |

`MONDAY_SCARIES_PROVIDER` and `MONDAY_SCARIES_MODEL` set defaults;
`--provider` and `--model` override them. Claude, OpenAI, and Gemini were
checked against the live APIs; Groq, OpenRouter, and custom endpoints are
covered by mocked tests only.

## Run

```bash
# Issues with any activity in the last 7 days (the default window)
monday-scaries pandas-dev/pandas

# A full GitHub link works too
monday-scaries https://github.com/pola-rs/polars

# Only issues opened in the last 7 days, at most 8 sent to the LLM
monday-scaries pandas-dev/pandas --new-only --limit 8

# Last day only, show the top 5
monday-scaries https://github.com/pytorch/pytorch --days 1 --limit 10 --top 5

# Another provider
monday-scaries pandas-dev/pandas --provider openai

# JSON for scripts (progress goes to stderr, so the file holds only JSON)
monday-scaries pandas-dev/pandas --limit 5 --json > report.json
```

| Option | Meaning |
|---|---|
| `repo` | `owner/repo` or `https://github.com/owner/repo`. Anything else is rejected before any request. |
| `--days N` | Activity window, 1-365 (default 7). Old issues with new activity are included. |
| `--new-only` | Only issues created inside the window. |
| `--limit N` | Most issues sent to the LLM, 1-200 (default 50). Caps cost. |
| `--top N` | Issues shown in the report (default 10). |
| `--json` | Print the same report as JSON. |
| `--provider`, `--model` | Choose the LLM. |
| `--no-color` | Plain text. Colors are also off when output is piped or `NO_COLOR` is set. |
| `--show-keys` | List which keys are set and where from. |
| `-v` | Debug logs (never include keys). |

### Example output

A real run on 2026-11-09 (`--no-color`, shortened where marked):

```text
$ monday-scaries pandas-dev/pandas --new-only --limit 8 --no-color
Classifying 7 issues with claude/claude-haiku-5-5...
  [1/7] #71978 bug, wrong_results
  ...
  [7/7] #71887 bug, wrong_results
pandas-dev/pandas · issues created in the last 7 days (since 2026-11-03)
Classified 7 of 103 open issues · 7 new since the baseline

 CRITICAL 0   HIGH 2   ? 0   MEDIUM 4   LOW 1
 installation 0 · input-output 6 · other 1

━━ HIGH (2) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 #71887   BUG: Series.str.get_dummies adds "NaN" / "" column for categorical / Arrow-backed str… NEW
         input-output · opened Nov 03 · 6 days
         wrong results
         Series.str.get_dummies gives wrong columns for categorical and Arrow-backed string dtypes,
         adding NaN or empty-string columns.

 #71941   BUG: to_sql writes timedelta64 columns in their own unit instead of nanoseconds (1000… NEW
         input-output · opened Nov 07 · 2 days
         wrong results, regression, workaround
         to_sql writes timedelta64 columns in their native unit, not nanoseconds, so values are
         1000x too small with the default microsecond unit.

━━ MEDIUM (4) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 #71953   BUG: Series.dt.total_seconds raises ArrowInvalid for duration[ns][pyarrow] longer tha… NEW
         input-output · opened Nov 08 · 1 day
         broken feature, regression, workaround, edge case
         Series.dt.total_seconds raises ArrowInvalid for duration[ns][pyarrow] values longer than
         about 104 days, though it worked in pandas 2.3.3.
 ... (3 more medium)

━━ LOW (1) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 #71916   BUG: excess precision when printing float32 values                                     NEW
         input-output · opened Nov 05 · 4 days
         cosmetic
         Pandas displays float32 values with excess precision, e.g. 100.099998 instead of 100.1,
         when printing Series.

Unclassified: none
Tokens: 16,032 · claude/claude-haiku-5-5
```

The progress lines go to stderr; the report goes to stdout. The line
under each title shows why the issue got its level: in #71941, the
regression (up) and the workaround (down) cancel out, so it stays at
the "wrong results" level, high.

**Severity**, worst first: critical, high, `?` (needs review), medium,
low. Then unrated issues, feature requests, and questions/docs/other.
Within each group, the oldest issue comes first. The line under each
issue says why it got its severity (a maintainer label, or the facts
used, such as "crash, regression, 12 👍").

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Report printed (or no issues matched) |
| 1 | GitHub error (network, timeout, bad response) |
| 2 | Bad input (repo, `--days`, `--limit`, ...) |
| 3 | Repository not found or private |
| 4 | GitHub rate limit reached |
| 5 | LLM problem: missing or rejected key, no credits, unknown model |
| 6 | The LLM failed partway; the partial report is printed with a warning |

## Update or remove

```bash
uv tool install --reinstall --compile-bytecode .   # after changing the code
uv tool uninstall monday-scaries
```

Run once without installing (from a checkout):

```bash
uvx --from . monday-scaries pandas-dev/pandas --limit 3
```

## Development and tests

```bash
uv sync                    # create .venv with dev tools
uv run pytest -q           # all tests: mocked, no network, no keys
uv run ruff check .        # lint (Google docstring style)
uv run ruff format --check .
uv build                   # wheel and sdist in dist/
```

The tests never call GitHub or an LLM and never read your key file. They
cover bad repo input, 404 (not found or private), no issues, pull
requests filtered out, the `--limit` cap and body truncation, broken or
invalid LLM JSON, a prompt-injection issue, timeouts, partial failure,
every provider error (401, 402, 403, 404, 429, 5xx), and that a key
never appears in any error message.
