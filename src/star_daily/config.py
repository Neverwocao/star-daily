"""配置：查询分片、限额参数、路径。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _root() -> Path:
    override = os.environ.get("STAR_DAILY_ROOT")
    return Path(override).resolve() if override else _DEFAULT_ROOT


@dataclass(frozen=True)
class SliceSpec:
    """一个 Search API 查询分片。

    stars 区间存在的唯一目的：Search API 每个查询最多返回 1000 条，
    按 star 区间切分后每片都能落到 1000 以内，避免召回被截断。

    created_mode=True 的分片不加 stars 条件，也不做区间细分——它按 stars
    降序取前 N 条，对"最近新建且涨得快"这个目标已经足够。
    """

    label: str
    base: str
    lo: int = 0
    hi: int = 0
    created_mode: bool = False

    def query(self) -> str:
        if self.created_mode:
            return self.base
        return f"{self.base} stars:{self.lo}..{self.hi}"


@dataclass
class Settings:
    token: str = ""
    db_path: Path = field(default_factory=lambda: _root() / "data" / "state.db")
    reports_dir: Path = field(default_factory=lambda: _root() / "reports")
    readme_path: Path = field(default_factory=lambda: _root() / "README.md")

    # 榜单
    top_n: int = 10
    # 增速榜的最低星标门槛，用于挡掉基数过小导致百分比失真的仓库
    speed_min_stars: int = 500

    # 快照保留窗口：超过这个天数的快照会被删除，控制 db 体积
    retention_days: int = 14
    # 基线快照最老可接受的天数，超过则认为断档太久、不参与排名
    baseline_max_age: int = 4
    # 冷启动估算窗口：库里没有任何历史时，用 created_at 以来的平均日增兜底
    cold_start_window: int = 7

    # 抓取
    per_page: int = 100
    max_pages: int = 3
    max_bisect_depth: int = 6
    # Search API 认证限额 30 次/分钟，2.0s 间隔正好卡在边界
    request_interval: float = 2.0
    max_retries: int = 3
    timeout: float = 30.0


def build_slices(today: date) -> list[SliceSpec]:
    """构造当天的查询分片。日期用相对偏移，保证长期不用改代码。"""
    new_since = (today - timedelta(days=7)).isoformat()
    push_since = (today - timedelta(days=1)).isoformat()
    return [
        # 新仓库：涨星最密集的区域
        SliceSpec("new", f"created:>={new_since}", created_mode=True),
        # 活跃老仓库，按 star 量级分层，保证每片都远小于 1000 条
        SliceSpec("small", f"pushed:>={push_since}", 500, 1999),
        SliceSpec("medium", f"pushed:>={push_since}", 2000, 9999),
        SliceSpec("large", f"pushed:>={push_since}", 10000, 49999),
        SliceSpec("huge", f"pushed:>={push_since}", 50000, 10_000_000),
    ]
