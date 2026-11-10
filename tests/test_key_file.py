"""Tests for the home-folder key file. Values never appear in output."""

import pytest

from monday_scaries import cli, config
from monday_scaries.config import key_status, load_key_file, parse_key_file

KEY = "sk-test-SECRET-999"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No keys in the environment unless a test sets them."""
    for name in config.allowed_variables() | {"ANTHROPIC_BASE_URL", "PATH_X"}:
        monkeypatch.delenv(name, raising=False)


def test_parses_names_quotes_export_and_comments():
    """Common .env spellings all work; empty values are skipped."""
    text = (
        "# my keys\n"
        f'ANTHROPIC_API_KEY="{KEY}"\n'
        "export OPENAI_API_KEY='sk-other'\n"
        "GROQ_API_KEY=\n"
        "\n"
    )
    assert parse_key_file(text) == {
        "ANTHROPIC_API_KEY": KEY,
        "OPENAI_API_KEY": "sk-other",
    }


def test_unknown_and_malformed_lines_are_ignored_without_values(caplog):
    """The file cannot redirect an SDK or set unrelated variables."""
    text = (
        f"ANTHROPIC_BASE_URL=https://evil.example\nPATH_X=1\nnot a line {KEY}\n"
    )
    assert parse_key_file(text) == {}
    assert KEY not in caplog.text and "evil" not in caplog.text


def test_a_pasted_key_used_as_a_name_is_not_echoed(caplog):
    """A key pasted on the left of = is not repeated in the warning."""
    assert parse_key_file(f"{KEY}=x\n") == {}
    assert KEY not in caplog.text


def test_environment_wins_over_the_file(tmp_path, monkeypatch):
    """A key already exported is not replaced by the file."""
    path = tmp_path / ".env"
    path.write_text(f"ANTHROPIC_API_KEY={KEY}\nOPENAI_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    loaded = load_key_file(path)
    assert loaded == {"OPENAI_API_KEY": "file"}
    assert config.os.environ["ANTHROPIC_API_KEY"] == "from-env"
    assert config.os.environ["OPENAI_API_KEY"] == "from-file"


def test_missing_file_is_fine(tmp_path):
    """No key file: nothing loaded, no error."""
    assert load_key_file(tmp_path / "nope.env") == {}


def test_show_keys_prints_sources_never_values(tmp_path, monkeypatch, capsys):
    """--show-keys lists names and where they came from."""
    path = tmp_path / ".env"
    path.write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    monkeypatch.setattr(cli, "KEY_FILE", path)
    monkeypatch.setattr(config, "KEY_FILE", path)
    assert cli.main(["--show-keys"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out and "set (from" in out
    assert "OPENAI_API_KEY" in out and "missing" in out
    assert KEY not in out


def test_key_status_reports_environment(monkeypatch):
    """A key set in the shell is reported as environment."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    line = next(s for s in key_status({}) if "OPENAI_API_KEY" in s)
    assert "set (environment)" in line and KEY not in line


def test_repo_still_required_without_show_keys():
    """Plain run with no repo is a usage error."""
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == 2
