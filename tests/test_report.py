"""Markdown 渲染的单元测试。"""

from __future__ import annotations

from datetime import date

from star_daily.ranking import Candidate
from star_daily.report import (
    ReportMeta,
    fmt_delta,
    fmt_speed,
    fmt_stars,
    render_daily,
    render_readme,
)


def _cand(**overrides) -> Candidate:
    base = dict(
        repo_id=1,
        full_name="acme/rocket",
        html_url="https://github.com/acme/rocket",
        description="a fast thing",
        language="Rust",
        stars=12_400,
        base_stars=12_000,
        delta=400,
        kind="exact",
        is_new=False,
    )
    base.update(overrides)
    return Candidate(**base)


def _meta(**overrides) -> ReportMeta:
    base = dict(
        day=date(2026, 11, 16),
        window_start="2026-11-15 00:00",
        window_end="2026-11-16 00:00",
        generated_at="2026-11-16 00:12 UTC",
        candidate_count=1842,
        slices=[("new", True, ""), ("small", True, "")],
        cold_start=False,
        db_size=1_048_576,
    )
    base.update(overrides)
    return ReportMeta(**base)


def test_fmt_stars_buckets():
    assert fmt_stars(999) == "999"
    assert fmt_stars(12_400) == "12.4k"
    assert fmt_stars(1_500_000) == "1.50M"


def test_fmt_delta_marks_estimation():
    assert fmt_delta(_cand(delta=1234, kind="exact")) == "+1,234"
    assert fmt_delta(_cand(delta=200, kind="estimated")) == "~+200"
    assert fmt_delta(_cand(delta=45, kind="cold_start")) == "≈+45"
    assert fmt_delta(_cand(delta=None, kind="unknown")) == "—"


def test_fmt_speed_scales_precision():
    assert fmt_speed(_cand(speed=1.5)) == "+150%"
    assert fmt_speed(_cand(speed=0.123)) == "+12.3%"
    assert fmt_speed(_cand(speed=0.0123)) == "+1.23%"


def test_render_contains_repo_and_weekday():
    text = render_daily(meta=_meta(), delta_rows=[_cand()], speed_rows=[])
    assert "acme/rocket" in text
    assert "2026年11月16日 星期一" in text
    assert "**+400**" in text


def test_render_escapes_pipe_in_description():
    # 竖线不转义会把 Markdown 表格撕成两半
    text = render_daily(
        meta=_meta(),
        delta_rows=[_cand(description="rust | go | zig")],
        speed_rows=[],
    )
    table_row = next(line for line in text.splitlines() if line.startswith("| 1 |"))
    assert "\\|" in table_row
    assert table_row.count(" | ") == 5  # 6 列表格 = 5 个分隔符


def test_render_truncates_long_description():
    text = render_daily(
        meta=_meta(), delta_rows=[_cand(description="x" * 300)], speed_rows=[]
    )
    assert "…" in text


def test_render_handles_empty_boards():
    text = render_daily(meta=_meta(), delta_rows=[], speed_rows=[])
    assert "今日没有可用的增量数据" in text
    assert "暂无数据" in text


def test_render_flags_incomplete_slices():
    meta = _meta(slices=[("new", True, ""), ("huge", False, "命中 3200 条，召回被截断")])
    text = render_daily(meta=meta, delta_rows=[_cand()], speed_rows=[])
    assert "数据不完整" in text
    assert "召回被截断" in text


def test_render_flags_cold_start():
    text = render_daily(meta=_meta(cold_start=True), delta_rows=[_cand()], speed_rows=[])
    assert "首次运行" in text


def test_render_marks_new_comer():
    text = render_daily(meta=_meta(), delta_rows=[_cand(is_new=True)], speed_rows=[])
    assert "| *[acme/rocket]" in text


def test_render_readme_links_latest_report():
    text = render_readme(
        meta=_meta(), delta_rows=[_cand()], latest_path="reports/2026-11-16.md"
    )
    assert "reports/2026-11-16.md" in text
    assert "acme/rocket" in text


def test_headings_follow_configured_top_n():
    text = render_daily(
        meta=_meta(top_n=5), delta_rows=[_cand()], speed_rows=[_cand(speed=0.12)]
    )
    assert "## 今日涨星 Top 5" in text
    assert "## 增速榜 Top 5" in text
    assert "Top 10" not in text


def test_default_top_n_is_thirty():
    assert ReportMeta(
        day=date(2026, 11, 16),
        window_start="2026-11-15 00:00",
        window_end="2026-11-16 00:00",
        generated_at="2026-11-16 00:12 UTC",
        candidate_count=1842,
    ).top_n == 30


def test_translated_description_wins_over_original():
    text = render_daily(
        meta=_meta(),
        delta_rows=[_cand(description="a fast thing", description_zh="一个很快的东西")],
        speed_rows=[],
    )
    assert "一个很快的东西" in text
    assert "a fast thing" not in text


def test_render_falls_back_to_original_description():
    text = render_daily(
        meta=_meta(),
        delta_rows=[_cand(description="a fast thing", description_zh=None)],
        speed_rows=[],
    )
    assert "a fast thing" in text


def test_render_flags_incomplete_translation():
    text = render_daily(
        meta=_meta(translate_failed=3), delta_rows=[_cand()], speed_rows=[]
    )
    assert "翻译不完整" in text
    assert "3 条" in text


def test_readme_mentions_translation_and_top_n():
    text = render_readme(
        meta=_meta(top_n=30), delta_rows=[_cand()], latest_path="reports/2026-11-16.md"
    )
    assert "Top 30" in text
    assert "简介统一翻译成中文" in text
    assert "translate.py" in text
