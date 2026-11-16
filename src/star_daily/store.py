"""SQLite 存储：仓库元信息、每日快照、上榜记录。"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS repo (
    id          INTEGER PRIMARY KEY,      -- GitHub repo.id，仓库改名/转移不丢历史
    full_name   TEXT NOT NULL,
    owner       TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    language    TEXT,
    html_url    TEXT NOT NULL,
    created_at  TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot (
    repo_id     INTEGER NOT NULL REFERENCES repo(id),
    day         TEXT NOT NULL,            -- 'YYYY-MM-DD'，UTC 自然日
    stars       INTEGER NOT NULL,
    forks       INTEGER,
    open_issues INTEGER,
    captured_at TEXT NOT NULL,            -- 实际执行时刻，便于排查延迟
    PRIMARY KEY (repo_id, day)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_day ON snapshot(day);

CREATE TABLE IF NOT EXISTS daily_rank (
    day     TEXT NOT NULL,
    rank    INTEGER NOT NULL,
    repo_id INTEGER NOT NULL,
    delta   INTEGER NOT NULL,
    board   TEXT NOT NULL DEFAULT 'delta',  -- delta | speed
    PRIMARY KEY (day, board, rank)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 单进程顺序写入，用 DELETE 模式避免留下 -wal/-shm 需要一起提交
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _repo_row(raw: dict[str, Any], day: str) -> tuple:
    owner = raw.get("owner") or {}
    return (
        int(raw["id"]),
        raw["full_name"],
        owner.get("login") or raw["full_name"].split("/")[0],
        raw.get("name") or raw["full_name"].split("/")[-1],
        raw.get("description"),
        raw.get("language"),
        raw["html_url"],
        raw.get("created_at"),
        day,
        day,
    )


def _snapshot_row(raw: dict[str, Any], day: str, captured_at: str) -> tuple:
    return (
        int(raw["id"]),
        day,
        int(raw["stargazers_count"]),
        raw.get("forks_count"),
        raw.get("open_issues_count"),
        captured_at,
    )


def write_batch(
    conn: sqlite3.Connection,
    repos: Iterable[dict[str, Any]],
    *,
    day: str,
    captured_at: str,
) -> int:
    """幂等写入：同一天重复运行会覆盖而不是报错。返回处理条数。"""
    repos = list(repos)
    if not repos:
        return 0

    conn.executemany(
        """
        INSERT INTO repo (id, full_name, owner, name, description, language,
                          html_url, created_at, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            full_name   = excluded.full_name,
            description = excluded.description,
            language    = excluded.language,
            html_url    = excluded.html_url,
            last_seen   = excluded.last_seen
        """,
        [_repo_row(r, day) for r in repos],
    )
    conn.executemany(
        """
        INSERT INTO snapshot (repo_id, day, stars, forks, open_issues, captured_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id, day) DO UPDATE SET
            stars       = excluded.stars,
            forks       = excluded.forks,
            open_issues = excluded.open_issues,
            captured_at = excluded.captured_at
        """,
        [_snapshot_row(r, day, captured_at) for r in repos],
    )
    conn.commit()
    return len(repos)


def prune(conn: sqlite3.Connection, *, today: date, retention_days: int) -> tuple[int, int]:
    """删除窗口外的快照，并清理没有任何快照的仓库。返回 (快照数, 仓库数)。"""
    cutoff = date.fromordinal(today.toordinal() - retention_days).isoformat()
    cur = conn.execute("DELETE FROM snapshot WHERE day < ?", (cutoff,))
    dropped_snaps = cur.rowcount
    cur = conn.execute("DELETE FROM repo WHERE id NOT IN (SELECT DISTINCT repo_id FROM snapshot)")
    dropped_repos = cur.rowcount
    conn.commit()
    return dropped_snaps, dropped_repos


def has_history_before(conn: sqlite3.Connection, day: str) -> bool:
    row = conn.execute("SELECT 1 FROM snapshot WHERE day < ? LIMIT 1", (day,)).fetchone()
    return row is not None


def load_candidates(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    """取当天全部快照，并 LEFT JOIN 出"最近一次早于今天的快照"作为基线。"""
    return conn.execute(
        """
        SELECT c.repo_id,
               c.stars,
               b.stars AS base_stars,
               b.day   AS base_day,
               CAST(julianday(?) - julianday(b.day) AS INTEGER) AS age_days,
               r.full_name,
               r.description,
               r.language,
               r.html_url,
               r.created_at
        FROM snapshot c
        JOIN repo r ON r.id = c.repo_id
        LEFT JOIN snapshot b
               ON b.repo_id = c.repo_id
              AND b.day = (
                    SELECT MAX(s2.day) FROM snapshot s2
                    WHERE s2.repo_id = c.repo_id AND s2.day < ?
                  )
        WHERE c.day = ?
        """,
        (day, day, day),
    ).fetchall()


def save_daily_rank(
    conn: sqlite3.Connection, day: str, board: str, entries: list[tuple[int, int]]
) -> None:
    conn.execute("DELETE FROM daily_rank WHERE day = ? AND board = ?", (day, board))
    conn.executemany(
        "INSERT INTO daily_rank (day, board, rank, repo_id, delta) VALUES (?, ?, ?, ?, ?)",
        [(day, board, i + 1, repo_id, delta) for i, (repo_id, delta) in enumerate(entries)],
    )
    conn.commit()


def load_previous_rank(conn: sqlite3.Connection, day: str, board: str) -> set[int]:
    """取今天之前最近一次的上榜 repo_id 集合，用于判定"新上榜"。"""
    row = conn.execute(
        "SELECT MAX(day) AS d FROM daily_rank WHERE day < ? AND board = ?", (day, board)
    ).fetchone()
    if not row or not row["d"]:
        return set()
    rows = conn.execute(
        "SELECT repo_id FROM daily_rank WHERE day = ? AND board = ?", (row["d"], board)
    ).fetchall()
    return {int(r["repo_id"]) for r in rows}


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")


def db_size_bytes(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0
