"""日增计算与榜单排序的单元测试。"""

from __future__ import annotations

from datetime import date, timedelta

from star_daily.ranking import (
    Candidate,
    build_candidates,
    compute_delta,
    dedupe_by_name,
    mark_new_comers,
    normalize_name,
    rank_by_delta,
    rank_by_speed,
)

TODAY = date(2026, 11, 16)


def test_exact_one_day():
    d = compute_delta(
        stars=1200, base_stars=1000, age_days=1, created_on=None, today=TODAY, max_age=4
    )
    assert (d.value, d.kind) == (200, "exact")


def test_negative_delta_keeps_sign():
    d = compute_delta(
        stars=900, base_stars=1000, age_days=1, created_on=None, today=TODAY, max_age=4
    )
    assert d.value == -100


def test_gap_is_normalized_to_daily_average():
    # 漏了 3 天，涨了 600 → 日均 200
    d = compute_delta(
        stars=1600, base_stars=1000, age_days=3, created_on=None, today=TODAY, max_age=4
    )
    assert (d.value, d.kind) == (200, "estimated")


def test_baseline_too_old_is_discarded():
    d = compute_delta(
        stars=5000, base_stars=1000, age_days=9, created_on=None, today=TODAY, max_age=4
    )
    assert d.value is None and d.kind == "unknown"


def test_repo_created_today_counts_full_stars():
    d = compute_delta(
        stars=850, base_stars=None, age_days=None, created_on=TODAY, today=TODAY, max_age=4
    )
    assert (d.value, d.kind) == (850, "first_day")


def test_old_repo_without_baseline_is_unknown():
    # 仓库早就存在，只是今天第一次被召回，增量不可知
    d = compute_delta(
        stars=90_000, base_stars=None, age_days=None,
        created_on=TODAY - timedelta(days=400), today=TODAY, max_age=4,
    )
    assert d.value is None and d.kind == "unknown"


def test_cold_start_uses_lifetime_average():
    d = compute_delta(
        stars=900, base_stars=None, age_days=None,
        created_on=TODAY - timedelta(days=3), today=TODAY, max_age=4,
        cold_start=True,
    )
    assert (d.value, d.kind) == (300, "cold_start")


def test_cold_start_respects_window():
    d = compute_delta(
        stars=9000, base_stars=None, age_days=None,
        created_on=TODAY - timedelta(days=30), today=TODAY, max_age=4,
        cold_start=True,
    )
    assert d.value is None


def test_baseline_on_or_after_today_is_rejected():
    d = compute_delta(
        stars=1000, base_stars=1000, age_days=0, created_on=None, today=TODAY, max_age=4
    )
    assert d.value is None


def _cand(repo_id: int, *, stars: int, delta: int | None, base: int | None = None) -> Candidate:
    c = Candidate(
        repo_id=repo_id,
        full_name=f"owner/repo{repo_id}",
        html_url=f"https://github.com/owner/repo{repo_id}",
        description="demo",
        language="Python",
        stars=stars,
        base_stars=base,
        delta=delta,
        kind="exact" if delta is not None else "unknown",
    )
    if delta is not None and base:
        c.speed = delta / base
    return c


def test_rank_by_delta_orders_and_breaks_ties_by_total_stars():
    cands = [
        _cand(1, stars=5000, delta=100),
        _cand(2, stars=90_000, delta=100),
        _cand(3, stars=1000, delta=300),
        _cand(4, stars=10_000, delta=None),
    ]
    ranked = rank_by_delta(cands, 10)
    assert [c.repo_id for c in ranked] == [3, 2, 1]


def test_rank_by_delta_respects_top_n():
    cands = [_cand(i, stars=1000, delta=i) for i in range(1, 20)]
    assert len(rank_by_delta(cands, 10)) == 10


def test_rank_by_speed_filters_small_bases():
    cands = [
        _cand(1, stars=600, delta=100, base=500),      # +20%
        _cand(2, stars=120, delta=100, base=20),       # +500% 但基数太小
        _cand(3, stars=2000, delta=200, base=1800),    # +11.1%
    ]
    ranked = rank_by_speed(cands, 10, min_stars=500)
    assert [c.repo_id for c in ranked] == [1, 3]


def test_mark_new_comers_skips_when_no_history():
    rows = [_cand(1, stars=100, delta=10)]
    mark_new_comers(rows, set())
    assert rows[0].is_new is False


def test_mark_new_comers_flags_missing_ids():
    rows = [_cand(1, stars=100, delta=10), _cand(2, stars=200, delta=20)]
    mark_new_comers(rows, {2})
    assert [c.is_new for c in rows] == [True, False]


def test_build_candidates_from_rows():
    rows = [
        {
            "repo_id": 42, "stars": 1500, "base_stars": 1000, "base_day": "2026-11-15",
            "age_days": 1, "full_name": "a/b", "description": "x", "language": "Go",
            "html_url": "https://github.com/a/b", "created_at": "2024-01-01T00:00:00Z",
        }
    ]
    out = build_candidates(rows, today=TODAY, max_age=4, cold_start=False)
    assert out[0].delta == 500
    assert out[0].speed == 0.5
    assert out[0].created_on == date(2024, 1, 1)


def test_normalize_name_strips_trailing_noise():
    assert normalize_name("a/Gemini-Config-Checker-") == "gemini-config-checker"
    assert normalize_name("b/gemini-config-checker-2") == "gemini-config-checker"
    assert normalize_name("c/Gemini_Config_Checker") == "gemini_config_checker"


def test_normalize_name_keeps_digits_that_carry_meaning():
    # n8n 的 8 在中间，不该被削
    assert normalize_name("n8n-io/n8n") == "n8n"
    # 归一后过短时退回原名，避免把 v2 / v3 这类仓库错误合并
    assert normalize_name("x/v2") == "v2"


def test_dedupe_keeps_strongest_and_counts_merged():
    cands = [
        _cand(1, stars=473, delta=473),
        _cand(2, stars=473, delta=473),
        _cand(3, stars=900, delta=900),
    ]
    for c, name in zip(cands, ["a/Thing-", "b/Thing-2", "c/Thing"]):
        c.full_name = name

    kept, merged = dedupe_by_name(cands)
    assert merged == 2
    assert len(kept) == 1
    assert kept[0].repo_id == 3


def test_dedupe_leaves_distinct_names_alone():
    cands = [_cand(1, stars=100, delta=10), _cand(2, stars=200, delta=20)]
    cands[0].full_name = "a/dify"
    cands[1].full_name = "b/dify-sandbox"
    kept, merged = dedupe_by_name(cands)
    assert merged == 0 and len(kept) == 2
