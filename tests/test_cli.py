"""入口相关的测试：.env 读取。"""

from __future__ import annotations

import os

from star_daily.cli import load_dotenv


def test_reads_token_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=ghp_abc123\n", encoding="utf-8")

    assert load_dotenv(env_file) is True
    assert os.environ["GITHUB_TOKEN"] == "ghp_abc123"


def test_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 这是注释\n\n  \nOTHER_KEY=skip\nGITHUB_TOKEN='quoted-value'\n",
        encoding="utf-8",
    )

    assert load_dotenv(env_file) is True
    assert os.environ["GITHUB_TOKEN"] == "quoted-value"


def test_existing_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=from-file\n", encoding="utf-8")

    assert load_dotenv(env_file) is True
    assert os.environ["GITHUB_TOKEN"] == "from-shell"


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert load_dotenv(tmp_path / "nope.env") is False
    assert "GITHUB_TOKEN" not in os.environ


def test_empty_value_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=\n", encoding="utf-8")

    assert load_dotenv(env_file) is False
    assert "GITHUB_TOKEN" not in os.environ
