"""GitHub Search API 客户端：限速、重试、分页、区间二分。

只使用官方 REST API（/search/repositories），不解析任何 HTML 页面。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import SliceSpec

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.github.com/search/repositories"
# Search API 单查询硬上限
SEARCH_RESULT_CAP = 1000
# primary rate limit 重置等待的容忍上限，超过就直接报错而不是傻等
MAX_RATE_LIMIT_WAIT = 120


class ApiError(RuntimeError):
    """不可恢复的 API 错误（查询语法错误、权限不足等）。"""


@dataclass
class SliceResult:
    """一个分片的抓取结果。complete=False 表示召回被截断。"""

    label: str
    repos: list[dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    complete: bool = True
    note: str = ""


class SearchClient:
    def __init__(
        self,
        token: str,
        *,
        interval: float = 2.0,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "star-daily/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(timeout=timeout, headers=headers)
        self._interval = interval
        self._max_retries = max_retries
        self._last_call = 0.0
        self.request_count = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SearchClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- 底层请求 ----------

    def _throttle(self) -> None:
        """串行 + 固定间隔，把速率压在 Search API 的 30 次/分钟以下。"""
        wait = self._interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _wait_hint(self, resp: httpx.Response) -> int | None:
        """从响应头里解析出"该等多久"，解析不出来返回 None。"""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(1, int(retry_after))
            except ValueError:
                pass

        if resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = resp.headers.get("X-RateLimit-Reset")
            if reset:
                try:
                    return max(1, int(reset) - int(time.time()) + 1)
                except ValueError:
                    pass
        return None

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        last_error = ""
        for attempt in range(self._max_retries + 1):
            self._throttle()
            self.request_count += 1
            try:
                resp = self._http.get(SEARCH_URL, params=params)
            except httpx.HTTPError as exc:
                last_error = f"network: {exc!r}"
                log.warning("请求失败（第 %d 次）：%s", attempt + 1, exc)
                time.sleep(min(2**attempt, 10))
                continue

            if resp.status_code == 200:
                return resp.json()

            # 查询语法错误重试也没用，直接失败
            if resp.status_code == 422:
                raise ApiError(f"查询被拒绝（422）：{params.get('q')!r} — {resp.text[:200]}")

            if resp.status_code in (403, 429):
                hint = self._wait_hint(resp)
                if hint is not None and hint <= MAX_RATE_LIMIT_WAIT:
                    log.warning("触发限额，等待 %ds 后重试", hint)
                    time.sleep(hint)
                    continue
                last_error = f"{resp.status_code}: {resp.text[:200]}"
                log.warning("限额等待过久或非限额 403：%s", last_error)
                time.sleep(min(2**attempt, 10))
                continue

            if resp.status_code >= 500:
                last_error = f"{resp.status_code}: {resp.text[:120]}"
                log.warning("服务端错误，退避重试：%s", last_error)
                time.sleep(min(2**attempt, 10))
                continue

            raise ApiError(f"未预期状态码 {resp.status_code}：{resp.text[:200]}")

        raise ApiError(f"请求重试 {self._max_retries} 次后仍失败（{last_error}）")

    # ---------- 检索 ----------

    def count(self, query: str) -> int:
        """只取 total_count，per_page=1 以节省配额和带宽。"""
        data = self._get(
            {"q": query, "sort": "stars", "order": "desc", "per_page": 1, "page": 1}
        )
        return int(data.get("total_count", 0))

    def search(
        self, query: str, *, per_page: int = 100, max_pages: int = 3
    ) -> tuple[list[dict[str, Any]], int]:
        """按 stars 降序翻页抓取，返回 (items, total_count)。"""
        items: list[dict[str, Any]] = []
        total = 0
        for page in range(1, max_pages + 1):
            data = self._get(
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                }
            )
            total = int(data.get("total_count", 0))
            batch = data.get("items") or []
            items.extend(batch)
            if len(batch) < per_page:
                break
            if len(items) >= min(total, SEARCH_RESULT_CAP):
                break
        return items, total

    # ---------- 分片收集 ----------

    def _collect_range(
        self,
        base: str,
        lo: int,
        hi: int,
        *,
        per_page: int,
        max_pages: int,
        max_depth: int,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], bool, str]:
        query = f"{base} stars:{lo}..{hi}"
        total = self.count(query)

        if total > SEARCH_RESULT_CAP:
            if hi - lo <= 1 or depth >= max_depth:
                items, _ = self.search(query, per_page=per_page, max_pages=max_pages)
                return (
                    items,
                    False,
                    f"stars:{lo}..{hi} 命中 {total} 条，已到细分下限，召回被截断",
                )
            mid = (lo + hi) // 2
            log.info("区间 %d..%d 命中 %d 条，二分 → %d..%d / %d..%d",
                     lo, hi, total, lo, mid, mid + 1, hi)
            left, ok_l, note_l = self._collect_range(
                base, lo, mid,
                per_page=per_page, max_pages=max_pages, max_depth=max_depth, depth=depth + 1,
            )
            right, ok_r, note_r = self._collect_range(
                base, mid + 1, hi,
                per_page=per_page, max_pages=max_pages, max_depth=max_depth, depth=depth + 1,
            )
            notes = [n for n in (note_l, note_r) if n]
            return left + right, ok_l and ok_r, "；".join(notes)

        items, _ = self.search(query, per_page=per_page, max_pages=max_pages)
        return items, True, ""

    def collect_slice(
        self,
        spec: SliceSpec,
        *,
        per_page: int,
        max_pages: int,
        max_depth: int = 6,
    ) -> SliceResult:
        if spec.created_mode:
            # 新库分片：按 stars 降序取前 N 条即可，total_count 超 1000 属正常
            items, total = self.search(spec.base, per_page=per_page, max_pages=max_pages)
            return SliceResult(spec.label, items, total, complete=True)

        items, complete, note = self._collect_range(
            spec.base, spec.lo, spec.hi,
            per_page=per_page, max_pages=max_pages, max_depth=max_depth,
        )
        return SliceResult(spec.label, items, len(items), complete=complete, note=note)
