"""SQLite 层的集成测试：幂等写入、基线查询、保留期清理。"""

from __future__ import annotations

from datetime import date

import pytest

from star_daily import store


def _repo(rid: int, stars: int, name: str = "acme/rocket") -> dict:
    return {
        "id": rid,
        "full_name": name,
        "owner": {"login": name.split("/")[0]},
        "name": name.split("/")[-1],
        "description": "demo",
        "language": "Rust",
        "html_url": f"https://github.com/{name}",
        "created_at": "2024-05-01T00:00:00Z",
        "stargazers_count": stars,
        "forks_count": 10,
        "open_issues_count": 3,
    }


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "state.db")
    yield connection
    connection.close()


def test_write_batch_is_idempotent(conn):
    store.write_batch(conn, [_repo(1, 1000)], day="2026-11-15", captured_at="t1")
    store.write_batch(conn, [_repo(1, 1200)], day="2026-11-15", captured_at="t2")

    rows = conn.execute("SELECT stars, captured_at FROM snapshot WHERE repo_id = 1").fetchall()
    assert len(rows) == 1
    assert rows[0]["stars"] == 1200
    assert rows[0]["captured_at"] == "t2"


def test_write_batch_keeps_separate_days(conn):
    store.write_batch(conn, [_repo(1, 1000)], day="2026-11-14", captured_at="t")
    store.write_batch(conn, [_repo(1, 1300)], day="2026-11-16", captured_at="t")

    rows = conn.execute("SELECT day, stars FROM snapshot ORDER BY day").fetchall()
    assert [r["day"] for r in rows] == ["2026-11-14", "2026-11-16"]


def test_load_candidates_uses_latest_earlier_snapshot_as_baseline(conn):
    store.write_batch(conn, [_repo(1, 1000)], day="2026-11-13", captured_at="t")
    store.write_batch(conn, [_repo(1, 1100)], day="2026-11-14", captured_at="t")
    store.write_batch(conn, [_repo(1, 1400)], day="2026-11-16", captured_at="t")

    rows = store.load_candidates(conn, "2026-11-16")
    assert len(rows) == 1
    assert rows[0]["stars"] == 1400
    assert rows[0]["base_stars"] == 1100     # 应当取最近的一次，而不是最早的
    assert rows[0]["age_days"] == 2


def test_load_candidates_without_baseline(conn):
    store.write_batch(conn, [_repo(1, 900)], day="2026-11-16", captured_at="t")
    rows = store.load_candidates(conn, "2026-11-16")
    assert rows[0]["base_stars"] is None
    assert rows[0]["age_days"] is None


def test_prune_drops_old_snapshots_and_orphan_repos(conn):
    store.write_batch(conn, [_repo(1, 1000)], day="2026-10-01", captured_at="t")
    store.write_batch(conn, [_repo(2, 500)], day="2026-11-15", captured_at="t")

    dropped_snaps, dropped_repos = store.prune(
        conn, today=date(2026, 11, 16), retention_days=14
    )
    assert dropped_snaps == 1
    assert dropped_repos == 1
    remaining = conn.execute("SELECT id FROM repo").fetchall()
    assert [r["id"] for r in remaining] == [2]


def test_has_history_before(conn):
    store.write_batch(conn, [_repo(1, 1000)], day="2026-11-15", captured_at="t")
    assert store.has_history_before(conn, "2026-11-16") is True
    assert store.has_history_before(conn, "2026-11-15") is False


def test_daily_rank_roundtrip(conn):
    store.save_daily_rank(conn, "2026-11-15", "delta", [(7, 300), (9, 200)])
    store.save_daily_rank(conn, "2026-11-16", "delta", [(9, 250)])

    previous = store.load_previous_rank(conn, "2026-11-16", "delta")
    assert previous == {7, 9}

    # 同一天重复写应当覆盖而不是叠加
    store.save_daily_rank(conn, "2026-11-16", "delta", [(11, 10)])
    rows = conn.execute(
        "SELECT repo_id FROM daily_rank WHERE day = '2026-11-16' AND board = 'delta'"
    ).fetchall()
    assert [r["repo_id"] for r in rows] == [11]
