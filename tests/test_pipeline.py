"""端到端测试：不联网，直接构造多天快照库，验证报告产出的真实性。

覆盖冷启动、真实差值、断档归一、新上榜标记、README 更新。
"""

from __future__ import annotations

from datetime import date

from star_daily import store
from star_daily.cli import build_report
from star_daily.config import Settings


def _repo(rid: int, stars: int, name: str, created_at: str = "2025-01-01T00:00:00Z") -> dict:
    return {
        "id": rid,
        "full_name": name,
        "owner": {"login": name.split("/")[0]},
        "name": name.split("/")[-1],
        "description": f"{name} 的简介",
        "language": "Python",
        "html_url": f"https://github.com/{name}",
        "created_at": created_at,
        "stargazers_count": stars,
        "forks_count": 10,
        "open_issues_count": 2,
    }


def _settings(tmp_path) -> Settings:
    return Settings(
        token="",
        db_path=tmp_path / "state.db",
        reports_dir=tmp_path / "reports",
        readme_path=tmp_path / "README.md",
        # 单元测试不联网：翻译层单独在 test_translate.py 用假 transport 覆盖
        translate=False,
    )


def _seed(settings: Settings, day: str, repos: list[dict]) -> None:
    conn = store.connect(settings.db_path)
    try:
        store.write_batch(conn, repos, day=day, captured_at=f"{day}T00:10:00+00:00")
    finally:
        conn.close()


def test_first_run_is_flagged_as_cold_start(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-15", [_repo(1, 1000, "acme/alpha")])

    report = build_report(s, date(2026, 11, 15))
    text = report.read_text(encoding="utf-8")
    assert "首次运行" in text
    # 老仓库没有基线，增量不可知，不能瞎编
    assert "今日没有可用的增量数据" in text


def test_second_day_uses_real_delta(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-15", [_repo(1, 1000, "acme/alpha"), _repo(2, 6000, "acme/beta")])
    build_report(s, date(2026, 11, 15))

    _seed(s, "2026-11-16", [_repo(1, 1300, "acme/alpha"), _repo(2, 6100, "acme/beta")])
    text = build_report(s, date(2026, 11, 16)).read_text(encoding="utf-8")

    assert "库中无历史快照" not in text
    assert "**+300**" in text       # alpha 1000 → 1300
    assert "**+100**" in text       # beta 6000 → 6100
    # 增速榜此时应有内容：30% 领先 1.67%
    assert "**+30.0%**" in text
    assert text.index("acme/alpha") < text.index("acme/beta", text.index("增速榜"))


def test_gap_between_snapshots_is_marked_estimated(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-15", [_repo(1, 1000, "acme/alpha")])
    # 11-16 缺失，直接跳到 11-17，涨了 400 → 日均 200
    _seed(s, "2026-11-17", [_repo(1, 1400, "acme/alpha")])

    text = build_report(s, date(2026, 11, 17)).read_text(encoding="utf-8")
    assert "~+200" in text


def test_repo_without_recent_baseline_is_excluded(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-01", [_repo(1, 1000, "acme/alpha")])
    _seed(s, "2026-11-16", [_repo(1, 2000, "acme/alpha")])

    text = build_report(s, date(2026, 11, 16)).read_text(encoding="utf-8")
    # 断档 15 天，差值不能当"一天"用
    assert "今日没有可用的增量数据" in text


def test_new_comer_is_flagged_against_previous_board(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-14", [
        _repo(1, 900, "acme/alpha"), _repo(2, 5900, "acme/beta"), _repo(3, 700, "acme/gamma"),
    ])
    _seed(s, "2026-11-15", [
        _repo(1, 1000, "acme/alpha"), _repo(2, 5950, "acme/beta"), _repo(3, 730, "acme/gamma"),
        # 该仓库 11-13 才建，11-15 首次进池且无基线 → 上不了榜
        _repo(4, 600, "acme/delta", created_at="2026-11-13T00:00:00Z"),
    ])
    build_report(s, date(2026, 11, 15))

    _seed(s, "2026-11-16", [
        _repo(1, 1100, "acme/alpha"), _repo(2, 5960, "acme/beta"), _repo(3, 740, "acme/gamma"),
        _repo(4, 1600, "acme/delta", created_at="2026-11-13T00:00:00Z"),
    ])
    text = build_report(s, date(2026, 11, 16)).read_text(encoding="utf-8")

    assert "*[acme/delta]" in text          # 本期新上榜
    assert "*[acme/alpha]" not in text      # 上期在榜


def test_readme_is_regenerated(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-15", [_repo(1, 1000, "acme/alpha")])
    _seed(s, "2026-11-16", [_repo(1, 1250, "acme/alpha")])
    build_report(s, date(2026, 11, 16))

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "GitHub 涨星日报" in readme
    assert "2026-11-16" in readme
    assert "acme/alpha" in readme
    assert "reports/2026-11-16.md" in readme


def test_duplicate_names_collapse_in_report(tmp_path):
    s = _settings(tmp_path)
    _seed(s, "2026-11-15", [
        _repo(1, 100, "a/Gemini-Checker-"), _repo(2, 100, "b/Gemini-Checker-2"),
        _repo(3, 100, "c/Gemini-Checker-7"),
    ])
    _seed(s, "2026-11-16", [
        _repo(1, 600, "a/Gemini-Checker-"), _repo(2, 590, "b/Gemini-Checker-2"),
        _repo(3, 580, "c/Gemini-Checker-7"),
    ])
    text = build_report(s, date(2026, 11, 16)).read_text(encoding="utf-8")

    assert "已合并 2 个同名仓库" in text
    assert text.count("Gemini-Checker-]") + text.count("Gemini-Checker-2]") \
         + text.count("Gemini-Checker-7]") == 1
