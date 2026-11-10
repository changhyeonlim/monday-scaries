"""Shared test setup."""

import pytest

from monday_scaries import cli, config


@pytest.fixture(autouse=True)
def no_real_key_file(tmp_path, monkeypatch):
    """Tests never read the developer's real ~/.monday_scaries/.env."""
    missing = tmp_path / "no-such-key-file.env"
    monkeypatch.setattr(cli, "KEY_FILE", missing)
    monkeypatch.setattr(config, "KEY_FILE", missing)
