"""命令行入口：collect / report / run。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import store, translate
from .api import SearchClient, SliceResult
from .config import Settings, build_slices, project_root, translate_enabled
from .ranking import (
    Candidate,
    build_candidates,
    dedupe_by_name,
    mark_new_comers,
    rank_by_delta,
    rank_by_speed,
)
from .report import ReportMeta, render_daily, render_readme

log = logging.getLogger("star_daily")


@dataclass
class CollectResult:
    slices: list[SliceResult]
    repo_count: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_dotenv(path: Path | None = None) -> bool:
    """从项目根的 .env 读取配置（环境变量已设置则不覆盖）。

    只为免掉"每次运行都要先 export"这一步，够用即可，不引入额外依赖。
    支持多行 `KEY=VALUE`，目前用到 `GITHUB_TOKEN` 与 `STAR_DAILY_TRANSLATE`；
    返回 GITHUB_TOKEN 是否可用。
    """
    env_file = path or project_root() / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    return bool(os.environ.get("GITHUB_TOKEN"))


def _resolve_day(raw: str | None) -> date:
    if raw:
        return date.fromisoformat(raw)
    return _utc_now().date()


def collect(settings: Settings, day: date) -> CollectResult:
    captured_at = _utc_now().isoformat(timespec="seconds")
    slices = build_slices(day)

    collected: dict[int, dict] = {}
    results: list[SliceResult] = []

    with SearchClient(
        settings.token,
        interval=settings.request_interval,
        max_retries=settings.max_retries,
        timeout=settings.timeout,
    ) as client:
        for spec in slices:
            log.info("抓取分片 %s：%s", spec.label, spec.query())
            try:
                result = client.collect_slice(
                    spec,
                    per_page=settings.per_page,
                    max_pages=settings.max_pages,
                    max_depth=settings.max_bisect_depth,
                )
            except Exception as exc:  # 单个分片失败不应拖垮整轮
                log.error("分片 %s 失败：%s", spec.label, exc)
                results.append(SliceResult(spec.label, [], 0, complete=False, note=f"请求失败：{exc}"))
                continue

            fresh = 0
            for repo in result.repos:
                rid = int(repo["id"])
                if rid not in collected:
                    collected[rid] = repo
                    fresh += 1
            extra = ""
            if spec.created_mode and result.total_count:
                extra = f"，窗口内共 {result.total_count} 个新仓库"
            log.info(
                "  %s：返回 %d 条（新增 %d）%s%s",
                spec.label, len(result.repos), fresh, extra,
                "" if result.complete else " · 召回截断",
            )
            results.append(result)

        requests_used = client.request_count

    log.info("本轮共发出 %d 次 API 请求，候选池 %d 个仓库", requests_used, len(collected))

    conn = store.connect(settings.db_path)
    try:
        store.write_batch(conn, collected.values(), day=day.isoformat(), captured_at=captured_at)
        dropped_snaps, dropped_repos = store.prune(
            conn, today=day, retention_days=settings.retention_days
        )
        if dropped_snaps:
            log.info("清理过期快照 %d 条，仓库 %d 个", dropped_snaps, dropped_repos)
        store.vacuum(conn)
    finally:
        conn.close()

    return CollectResult(slices=results, repo_count=len(collected))


def _apply_translations(
    settings: Settings, conn, candidates: list[Candidate]
) -> tuple[int, int]:
    """把榜上出现的简介译成中文，返回 (已译条数, 回退英文条数)。

    只翻榜单行而不是整个候选池：一次运行几十条，接口压力小、报告也够用。
    译文写进快照库缓存，下一期同一仓库不用再翻。
    """
    if not settings.translate:
        return 0, 0

    texts = [
        cand.description
        for cand in candidates
        if cand.description and translate.needs_translation(cand.description)
    ]
    if not texts:
        return 0, 0

    cache = store.load_translations(conn, [translate.source_hash(t) for t in texts])
    with translate.Translator(
        timeout=settings.translate_timeout,
        max_requests=settings.translate_max_requests,
    ) as translator:
        mapping, fresh, stats = translate.translate_texts(
            texts, translator=translator, cache=cache
        )
        requests_made = translator.requests_made

    if fresh:
        store.save_translations(
            conn, fresh, captured_at=_utc_now().isoformat(timespec="seconds")
        )

    for cand in candidates:
        if cand.description:
            cand.description_zh = mapping.get(cand.description.strip())

    if stats.failed:
        log.warning(
            "简介翻译不完整：%d 条回退英文原文（缓存命中 %d 条）",
            stats.failed, stats.from_cache,
        )
    else:
        log.info(
            "简介翻译完成：%d 条中文（缓存命中 %d 条，新发请求 %d 次）",
            stats.translated, stats.from_cache, requests_made,
        )
    return stats.translated, stats.failed


def build_report(settings: Settings, day: date, *, collect_result: CollectResult | None = None) -> Path:
    day_str = day.isoformat()
    conn = store.connect(settings.db_path)
    try:
        cold_start = not store.has_history_before(conn, day_str)
        rows = store.load_candidates(conn, day_str)
        if not rows:
            raise SystemExit(f"{day_str} 没有快照数据，先执行 collect")

        candidates = build_candidates(
            rows,
            today=day,
            max_age=settings.baseline_max_age,
            cold_start=cold_start,
            cold_start_window=settings.cold_start_window,
        )
        candidates, merged = dedupe_by_name(candidates)
        if merged:
            log.info("合并了 %d 个同名仓库", merged)

        delta_rows = rank_by_delta(candidates, settings.top_n)
        speed_rows = rank_by_speed(candidates, settings.top_n, min_stars=settings.speed_min_stars)

        mark_new_comers(delta_rows, store.load_previous_rank(conn, day_str, "delta"))
        mark_new_comers(speed_rows, store.load_previous_rank(conn, day_str, "speed"))

        translated, translate_failed = _apply_translations(
            settings, conn, delta_rows + speed_rows
        )

        slices = (
            [(s.label, s.complete, s.note) for s in collect_result.slices]
            if collect_result
            else []
        )
        meta = ReportMeta(
            day=day,
            window_start=f"{(day - timedelta(days=1)).isoformat()} 00:00",
            window_end=f"{day_str} 00:00",
            generated_at=_utc_now().strftime("%Y-%m-%d %H:%M UTC"),
            candidate_count=len(rows),
            slices=slices,
            cold_start=cold_start,
            db_size=store.db_size_bytes(settings.db_path),
            speed_min_stars=settings.speed_min_stars,
            merged_duplicates=merged,
            top_n=settings.top_n,
            translate_enabled=settings.translate,
            translated=translated,
            translate_failed=translate_failed,
        )

        body = render_daily(meta=meta, delta_rows=delta_rows, speed_rows=speed_rows)

        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = settings.reports_dir / f"{day_str}.md"
        # newline="\n"：不指定的话 Windows 会写成 CRLF，每天提交都会在 git 里
        # 触发换行符转换警告，而且和 Actions（Linux）产出的文件不一致
        report_path.write_text(body, encoding="utf-8", newline="\n")

        rel_path = f"reports/{day_str}.md"
        readme = render_readme(meta=meta, delta_rows=delta_rows, latest_path=rel_path)
        settings.readme_path.write_text(readme, encoding="utf-8", newline="\n")

        store.save_daily_rank(
            conn, day_str, "delta", [(c.repo_id, c.delta or 0) for c in delta_rows]
        )
        store.save_daily_rank(
            conn, day_str, "speed", [(c.repo_id, c.delta or 0) for c in speed_rows]
        )
    finally:
        conn.close()

    log.info("报告已写入 %s（增量榜 %d 条 / 增速榜 %d 条）",
             report_path, len(delta_rows), len(speed_rows))
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="star-daily", description="GitHub 涨星日报生成器"
    )
    parser.add_argument(
        "command", nargs="?", default="run", choices=["collect", "report", "run"],
        help="collect=只抓取入库，report=只重出报告，run=两步都做（默认）",
    )
    parser.add_argument("--today", help="以指定日期运行（YYYY-MM-DD），用于补跑")
    parser.add_argument(
        "--no-translate", action="store_true",
        help="跳过简介翻译，简介保留英文原文（离线补跑或接口不可用时用）",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
        stream=sys.stderr,
    )

    day = _resolve_day(args.today)
    had_token_in_env = bool(os.environ.get("GITHUB_TOKEN"))
    load_dotenv()
    settings = Settings(
        token=os.environ.get("GITHUB_TOKEN", ""),
        translate=translate_enabled() and not args.no_translate,
    )

    if not settings.translate:
        log.info("简介翻译已关闭（STAR_DAILY_TRANSLATE=0 或 --no-translate），简介保留原文")

    if not settings.token:
        log.warning("未设置 GITHUB_TOKEN，将使用未认证限额（Search API 仅 10 次/分钟，本轮会明显变慢）")
        log.warning("提示：把 .env.example 复制为 .env 并填入 token，之后就不用再配")
    elif not had_token_in_env:
        log.info("已从 .env 读取 GITHUB_TOKEN")

    log.info("运行日期 %s（UTC 自然日）", day.isoformat())

    if args.command == "collect":
        collect(settings, day)
    elif args.command == "report":
        build_report(settings, day)
    else:
        result = collect(settings, day)
        build_report(settings, day, collect_result=result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
