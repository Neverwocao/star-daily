"""日增计算与榜单排序。全部是纯函数，方便测试。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

DeltaKind = Literal["exact", "estimated", "cold_start", "first_day", "unknown"]


@dataclass
class Candidate:
    repo_id: int
    full_name: str
    html_url: str
    description: str | None
    language: str | None
    stars: int
    base_stars: int | None = None
    age_days: int | None = None
    created_on: date | None = None

    delta: int | None = None
    kind: DeltaKind = "unknown"
    speed: float | None = None
    is_new: bool = False


@dataclass(frozen=True)
class Delta:
    """一次日增判定的结果。

    value=None 表示无法给出可信数字，这类仓库不参与排名。
    """

    value: int | None
    kind: DeltaKind
    age_days: int | None = None


def parse_day(value: str | None) -> date | None:
    """解析 GitHub 的 ISO8601 时间戳，只取日期部分。"""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def compute_delta(
    *,
    stars: int,
    base_stars: int | None,
    age_days: int | None,
    created_on: date | None,
    today: date,
    max_age: int,
    cold_start: bool = False,
    cold_start_window: int = 7,
) -> Delta:
    """计算日增。

    优先级：有基线 → 用真实差值；没有基线但仓库是今天创建的 → 全量即增量；
    首次运行且能算出仓库年龄 → 用生命周期平均日增兜底（标 cold_start）。
    """
    if base_stars is None:
        if created_on == today:
            return Delta(stars, "first_day", 0)
        if cold_start and created_on is not None:
            age = (today - created_on).days
            if 1 <= age <= cold_start_window:
                return Delta(round(stars / age), "cold_start", age)
        return Delta(None, "unknown", None)

    if age_days is None or age_days < 1:
        # 基线日期异常（等于或晚于今天），不可信
        return Delta(None, "unknown", age_days)

    if age_days > max_age:
        # 断档太久，差值已不能代表"一天"，宁可弃权
        return Delta(None, "unknown", age_days)

    raw = stars - base_stars
    if age_days == 1:
        return Delta(raw, "exact", 1)
    # 中间漏了一天以上，按日均摊平并标记为估算
    return Delta(round(raw / age_days), "estimated", age_days)


def build_candidates(
    rows,
    *,
    today: date,
    max_age: int,
    cold_start: bool,
    cold_start_window: int = 7,
) -> list[Candidate]:
    """把数据库行转成带 delta 的候选对象。"""
    out: list[Candidate] = []
    for row in rows:
        cand = Candidate(
            repo_id=int(row["repo_id"]),
            full_name=row["full_name"],
            html_url=row["html_url"],
            description=row["description"],
            language=row["language"],
            stars=int(row["stars"]),
            base_stars=int(row["base_stars"]) if row["base_stars"] is not None else None,
            age_days=int(row["age_days"]) if row["age_days"] is not None else None,
            created_on=parse_day(row["created_at"]),
        )
        result = compute_delta(
            stars=cand.stars,
            base_stars=cand.base_stars,
            age_days=cand.age_days,
            created_on=cand.created_on,
            today=today,
            max_age=max_age,
            cold_start=cold_start,
            cold_start_window=cold_start_window,
        )
        cand.delta = result.value
        cand.kind = result.kind
        if result.age_days is not None:
            cand.age_days = result.age_days
        if (
            cand.delta is not None
            and cand.delta > 0
            and cand.base_stars is not None
            and cand.base_stars > 0
        ):
            cand.speed = cand.delta / cand.base_stars
        out.append(cand)
    return out


def rank_by_delta(candidates: list[Candidate], top_n: int) -> list[Candidate]:
    """绝对增量榜。同增量时星标多的在前，避免顺序随机。"""
    pool = [c for c in candidates if c.delta is not None]
    pool.sort(key=lambda c: (c.delta or 0, c.stars), reverse=True)
    return pool[:top_n]


def rank_by_speed(
    candidates: list[Candidate], top_n: int, *, min_stars: int
) -> list[Candidate]:
    """增速榜。要求有真实基线，挡掉小基数造成的百分比失真。"""
    pool = [
        c
        for c in candidates
        if c.speed is not None and c.base_stars is not None and c.base_stars >= min_stars
    ]
    pool.sort(key=lambda c: (c.speed or 0, c.delta or 0), reverse=True)
    return pool[:top_n]


def mark_new_comers(ranked: list[Candidate], previous_ids: set[int]) -> None:
    """标记"新上榜"。previous_ids 为空说明没有上一期数据，此时不标记。"""
    if not previous_ids:
        return
    for c in ranked:
        c.is_new = c.repo_id not in previous_ids


_TRAILING_JUNK = re.compile(r"[-_.\d]+$")


def normalize_name(full_name: str) -> str:
    """把仓库名归一成"项目主干"，用于识别批量生成的雷同仓库。

    去掉结尾的数字和分隔符：Gemini-Config-Checker- / gemini-config-checker-2
    都会落到 gemini-config-checker。归一后过短（如 v2）则不合并，避免误伤。
    """
    name = full_name.split("/")[-1].lower()
    core = _TRAILING_JUNK.sub("", name).strip("-_.")
    return core if len(core) >= 3 else name


def dedupe_by_name(candidates: list[Candidate]) -> tuple[list[Candidate], int]:
    """同名仓库只保留最强的一个。

    GitHub 上存在大量"同一模板批量建号"的仓库——名字雷同、描述一字不差、
    星标数量完全相同，且因为都建在最近几天会集体冲进新库分片的高位。
    不做合并的话，一次榜单能被同一项目占掉七八个位置。

    返回 (去重后的候选, 被合并的数量)。
    """
    best: dict[str, Candidate] = {}
    merged = 0
    for cand in candidates:
        key = normalize_name(cand.full_name)
        current = best.get(key)
        if current is None:
            best[key] = cand
            continue
        merged += 1
        if (cand.stars, cand.delta or 0) > (current.stars, current.delta or 0):
            best[key] = cand
    return list(best.values()), merged
