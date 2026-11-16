"""Markdown 渲染。不碰网络和数据库，输入候选列表，输出文本。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .ranking import Candidate

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")


@dataclass
class ReportMeta:
    day: date
    window_start: str          # ISO8601 UTC
    window_end: str            # ISO8601 UTC
    generated_at: str
    candidate_count: int       # 去重后的候选池大小
    slices: list[tuple[str, bool, str]] = field(default_factory=list)
    cold_start: bool = False
    db_size: int = 0
    speed_min_stars: int = 500
    merged_duplicates: int = 0
    # 榜单标题按这个数字动态生成，避免候选不足时标题说谎
    top_n: int = 30
    translate_enabled: bool = True
    translated: int = 0        # 简介成功译成中文的条数（含缓存命中）
    translate_failed: int = 0  # 简介翻译失败、回退英文原文的条数


def fmt_stars(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_delta(c: Candidate) -> str:
    if c.delta is None:
        return "—"
    value = c.delta
    sign = "+" if value >= 0 else "−"
    body = f"{sign}{abs(value):,}"
    if c.kind == "estimated":
        return f"~{body}"
    if c.kind == "cold_start":
        return f"≈{body}"
    return body


def fmt_speed(c: Candidate) -> str:
    if c.speed is None:
        return "—"
    pct = c.speed * 100
    if pct >= 100:
        return f"+{pct:.0f}%"
    if pct >= 10:
        return f"+{pct:.1f}%"
    return f"+{pct:.2f}%"


def _cell(text: str | None, limit: int = 64) -> str:
    """把任意文本塞进 Markdown 表格单元格：转义竖线、压平换行、截断。"""
    if not text:
        return ""
    flat = " ".join(text.split())
    flat = flat.replace("|", "\\|")
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


def _repo_cell(c: Candidate) -> str:
    mark = "*" if c.is_new else ""
    return f"{mark}[{c.full_name}]({c.html_url})"


def _description(c: Candidate) -> str:
    """优先用中文译文；没译出来就回退英文原文，绝不空着。"""
    return c.description_zh or c.description or ""


def _delta_table(rows: list[Candidate]) -> list[str]:
    lines = [
        "| # | 仓库 | 今日 | 总星 | 语言 | 简介 |",
        "|---:|------|-----:|-----:|------|------|",
    ]
    for i, c in enumerate(rows, 1):
        lines.append(
            f"| {i} | {_repo_cell(c)} | **{fmt_delta(c)}** | {fmt_stars(c.stars)} "
            f"| {_cell(c.language, 18) or '—'} | {_cell(_description(c))} |"
        )
    return lines


def _speed_table(rows: list[Candidate]) -> list[str]:
    lines = [
        "| # | 仓库 | 增速 | 今日 | 总星 | 语言 | 简介 |",
        "|---:|------|-----:|-----:|-----:|------|------|",
    ]
    for i, c in enumerate(rows, 1):
        lines.append(
            f"| {i} | {_repo_cell(c)} | **{fmt_speed(c)}** | {fmt_delta(c)} "
            f"| {fmt_stars(c.stars)} | {_cell(c.language, 18) or '—'} | {_cell(_description(c))} |"
        )
    return lines


def _slice_summary(meta: ReportMeta) -> str:
    total = len(meta.slices)
    if total == 0:
        return ""
    ok = sum(1 for _, complete, _ in meta.slices if complete)
    if ok == total:
        return f"分片 {ok}/{total} 完整"
    return f"分片 {ok}/{total} 完整（有遗漏）"


def _headers(meta: ReportMeta) -> list[str]:
    day_cn = f"{meta.day.year}年{meta.day.month}月{meta.day.day}日"
    weekday = WEEKDAYS[meta.day.weekday()]
    parts = [f"{meta.candidate_count:,} 个仓库"]
    summary = _slice_summary(meta)
    if summary:
        parts.append(summary)
    if meta.merged_duplicates:
        parts.append(f"已合并 {meta.merged_duplicates} 个同名仓库")

    lines = [
        f"# GitHub 涨星日报 · {day_cn} 星期{weekday}",
        "",
        f"> 数据窗口：{meta.window_start} → {meta.window_end} (UTC)  ",
        f"> 候选池：{' · '.join(parts)}  ",
        f"> 生成时间：{meta.generated_at}",
    ]
    if meta.cold_start:
        # 用空引用行断开，否则会和上一行渲染成同一段
        lines.append(">")
        lines.append(
            "> **首次运行，库中无历史快照**：部分增量为仓库生命周期平均日增（`≈` 标记），"
            "非真实当日增长。次日开始自动切换为真实差值。"
        )
    if meta.translate_enabled and meta.translate_failed:
        lines.append(">")
        lines.append(
            f"> **翻译不完整**：{meta.translate_failed} 条简介未能翻译，保留英文原文。"
        )
    if any(not complete for _, complete, _ in meta.slices):
        lines.append("")
        lines.append("**数据不完整**：")
        for label, complete, note in meta.slices:
            if not complete:
                lines.append(f"- `{label}`：{note or '召回被截断'}")
    return lines


def render_daily(
    *,
    meta: ReportMeta,
    delta_rows: list[Candidate],
    speed_rows: list[Candidate],
) -> str:
    lines = _headers(meta)
    lines += ["", f"## 今日涨星 Top {meta.top_n}", ""]
    lines += _delta_table(delta_rows) if delta_rows else ["_今日没有可用的增量数据。_"]

    lines += ["", f"## 增速榜 Top {meta.top_n}", ""]
    if speed_rows:
        lines += _speed_table(speed_rows)
    else:
        lines += ["_暂无数据（增速榜需要至少一天的历史基线，且基数 ≥ 500 星）。_"]

    lines += ["", "## 数据说明", ""]
    lines += [
        "- 数据源为 GitHub 官方 REST API（`/search/repositories`），不抓取任何网页。",
        "- 「今日」= 与最近一次历史快照的 star 差值；正常为 24 小时，用 `~` 标记的是断档后按日均摊平的估算值。",
        "- `*` 表示本期新上榜（与上一期榜单对比）。",
        f"- 两张榜各取前 {meta.top_n} 名（`Settings.top_n`）。",
        f"- 增速榜仅统计基数 ≥ {meta.speed_min_stars} 星的仓库，避免小分母放大百分比。",
        "- 同名仓库（去掉结尾数字与分隔符后相同）只保留最强的一个，用于压掉同一项目批量建号的霸榜。",
        "- 简介统一译为中文：首选 Google translate 免费端点，失败回退 MyMemory；译文按原文哈希缓存进快照库，"
        "已是中文的跳过，接口不可用时保留英文原文。",
        "- 分片查询用于绕开 Search API 单查询 1000 条上限；分片未全覆盖时会在顶部提示。",
    ]
    lines += ["", "---", "", f"_由 star-daily 自动生成 · 快照库 {meta.db_size / 1024:.0f} KB_", ""]
    return "\n".join(lines)


def render_readme(*, meta: ReportMeta, delta_rows: list[Candidate], latest_path: str) -> str:
    day = meta.day.isoformat()
    lines = [
        "# GitHub 涨星日报",
        "",
        "每天定时调用 GitHub 官方 Search API 抓取候选仓库，写入 SQLite 快照库，",
        f"与上一次快照对比算出日增 star，输出 Top {meta.top_n} 榜单并自动提交回本仓库。",
        "榜单里的仓库简介统一翻译成中文，不用再对着英文猜。",
        "",
        "## 最新一期 · " + day,
        "",
    ]
    lines += _delta_table(delta_rows) if delta_rows else ["_本期暂无数据。_"]
    lines += [
        "",
        f"完整报告（含增速榜与数据说明）：[`{latest_path}`]({latest_path})",
        "",
        "---",
        "",
        "## 工作原理",
        "",
        "```",
        "Search API 5 个分片召回 → 去重 → UPSERT 当日快照(SQLite)",
        "                                    ↓",
        f"              读取最近可用基线 → 计算日增 → 排序 Top {meta.top_n}",
        "                                    ↓",
        "               榜单简介翻译成中文（缓存命中则不发请求）",
        "                                    ↓",
        "                     渲染 Markdown → commit 回仓库",
        "```",
        "",
        "- **为什么需要快照**：GitHub 没有任何 star 历史接口，日增只能靠两次快照相减，因此首次运行无法出榜。",
        "- **候选池**：`created:>=7天前` 抓新仓库，叠加以 star 区间分层的 `pushed:>=1天前` 抓活跃老仓库。",
        "- **1000 条上限**：任一查询命中超过 1000 条时自动二分 star 区间，直到每片都能完整召回。",
        "- **断档处理**：基线取「最近一次早于今天的快照」而非硬编码 T-1；间隔 2~4 天按日均摊平并标 `~`，超过 4 天弃权。",
        "- **同名合并**：按去掉结尾数字与分隔符后的名称合并批量建号的雷同仓库，只保留最强的一个。",
        "- **简介翻译**：榜单行才翻译，按原文哈希缓存进快照库，同一句简介跨天只翻一次；"
        "Google 免费端点不可用时退到 MyMemory，都失败就保留英文原文并在报告顶部标注；"
        "连续失败会自动停手，不会拖慢出报。",
        "- **榜单条数**：默认两张榜各 30 条，由 `Settings.top_n` 控制。",
        "",
        "## 部署到自己的仓库",
        "",
        "1. Fork 或新建仓库，推入本目录全部文件。",
        "2. 确认 `Settings → Actions → General → Workflow permissions` 勾选 **Read and write permissions**，"
        "否则 bot 无法提交结果。",
        "3. 无需配置任何 Secret —— 内置 `GITHUB_TOKEN` 已足够（公开数据读取 + 对本仓库写入）。"
        "简介翻译走免费接口，同样不需要 key；若运行环境访问不了翻译接口，简介会保留英文原文并在报告顶部标注，不影响出榜。",
        "4. 默认每天 UTC 00:10（北京时间 08:10）运行，也可在 Actions 页面手动触发并指定补跑日期。",
        "",
        "> 若默认分支开启了分支保护，需放行 `github-actions[bot]` 直接推送，否则提交步骤会失败。",
        "",
        "## 一键运行（Windows）",
        "",
        "双击 `run-daily.bat` 即可。它会自检 Python 版本、首次运行时自动装依赖、生成报告并打开 `reports/` 目录。",
        "想免去每次配 token，把 `.env.example` 复制为 `.env` 并填入 token —— `.env` 已被 gitignore 排除。",
        "",
        "## 本地运行",
        "",
        "需要 Python 3.10 或更新版本。",
        "",
        "```bash",
        "pip install -e .",
        "export GITHUB_TOKEN=<your_token>   # 只需要 public_repo 读权限",
        "python -m star_daily run           # 抓取 + 出报告",
        "python -m star_daily report        # 只用已有数据重出报告",
        "STAR_DAILY_TRANSLATE=0 python -m star_daily report   # 离线补跑：跳过简介翻译",
        "```",
        "",
        "```bash",
        "pytest                             # 68 个测试：日增计算、断档归一、同名合并、渲染转义、幂等写入、.env 读取、简介翻译",
        "```",
        "",
        "## 目录",
        "",
        "| 路径 | 说明 |",
        "|------|------|",
        "| `src/star_daily/api.py` | Search API 客户端：限速、重试、分页、区间二分 |",
        "| `src/star_daily/store.py` | SQLite schema、幂等写入、基线查询 |",
        "| `src/star_daily/ranking.py` | 日增计算与榜单排序（纯函数） |",
        "| `src/star_daily/report.py` | Markdown 渲染（纯函数） |",
        "| `src/star_daily/cli.py` | `collect` / `report` / `run` 入口 |",
        "| `src/star_daily/translate.py` | 简介中文翻译：免费接口 + 译文缓存 + 失败回退 |",
        "| `data/state.db` | 快照库，随每次运行提交，保留最近 14 天 |",
        "| `reports/` | 历史日报，永久留存 |",
        "| `examples/sample-report.md` | 报告格式预览（样例数据） |",
        "",
    ]
    return "\n".join(lines)
