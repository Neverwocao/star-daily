"""简介翻译：免费接口 + 本地缓存 + 失败回退英文原文。

项目要在 GitHub Actions 里零 Secret 自动跑，所以不接需要 key 的商业 API：
首选 Google translate 的免费端点（gtx），失败退到 MyMemory，两者都不可用时
保留英文原文，报告照常生成。翻译只是可读性优化，绝不能拖垮出榜。

译文按「原文哈希」缓存进快照库，同一句简介跨天只翻一次；已经是中文的跳过。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

import httpx

log = logging.getLogger("star_daily")

GOOGLE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"

# 基本汉字 + 扩展 A + 兼容区，够判断"这段文字是不是中文"
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN = re.compile(r"[A-Za-z]")


def source_hash(text: str) -> str:
    """缓存键：按原文内容哈希，跨天复用同一句简介的译文。"""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def has_chinese(text: str | None) -> bool:
    """中文字符占到 1/4 就认为原文已经是中文（中英混排的简介也算）。"""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return len(_CJK.findall(stripped)) * 4 >= len(stripped)


def needs_translation(text: str | None) -> bool:
    """只翻「有英文、且不是中文」的简介；纯符号、纯数字的没必要翻。"""
    if not text or not text.strip():
        return False
    if has_chinese(text):
        return False
    return bool(_LATIN.search(text))


def parse_google(payload: object) -> str | None:
    """解析 gtx 返回：[[["译文","原文",...], ["","",...]], ...]，多段要拼起来。"""
    try:
        chunks = payload[0]  # type: ignore[index]
        parts = [chunk[0] for chunk in chunks if chunk and isinstance(chunk[0], str)]
    except (IndexError, KeyError, TypeError):
        return None
    text = "".join(parts).strip()
    return text or None


def parse_mymemory(payload: object) -> str | None:
    """解析 MyMemory 返回：{"responseStatus":200,"responseData":{"translatedText":...}}。"""
    try:
        status = int(payload.get("responseStatus", 0))  # type: ignore[union-attr]
        text = (payload["responseData"].get("translatedText") or "").strip()  # type: ignore[index]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if status != 200 or not text:
        return None
    # 超出免费额度时，它会把警告塞进 translatedText 当作「译文」
    if "MYMEMORY WARNING" in text.upper() or "QUOTA" in text.upper():
        return None
    return text


@dataclass
class TranslationStats:
    """一轮翻译的账本，写进报告用来提示翻译是否完整。"""

    translated: int = 0   # 最终拿到中文的条数（含缓存命中）
    from_cache: int = 0
    failed: int = 0       # 接口没给出结果、需要回退英文的条数


class Translator:
    """一次运行共用一个 httpx.Client，并带请求上限，避免拖慢出报。"""

    # 连续失败这么多次就认定接口全挂了：停止后续请求，别把一轮出报拖成几十分钟
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        max_requests: int = 120,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_requests = max_requests
        self.requests_made = 0
        self._consecutive_failures = 0
        self._client = client
        self._owns_client = client is None

    def __enter__(self) -> "Translator":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "star-daily/0.1"},
            )
            self._owns_client = True
        return self._client

    def _get_json(self, url: str, params: dict[str, str]) -> object:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _google(self, text: str) -> str | None:
        return parse_google(
            self._get_json(
                GOOGLE_ENDPOINT,
                {"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text},
            )
        )

    def _mymemory(self, text: str) -> str | None:
        return parse_mymemory(
            self._get_json(MYMEMORY_ENDPOINT, {"q": text, "langpair": "en|zh-CN"})
        )

    def _note_failure(self) -> bool:
        """记一次失败；返回是否已达熔断阈值。"""
        self._consecutive_failures += 1
        if self._consecutive_failures < self.MAX_CONSECUTIVE_FAILURES:
            return False
        log.warning(
            "翻译接口连续失败 %d 次，本轮起改为保留英文原文",
            self._consecutive_failures,
        )
        return True

    def translate(self, text: str) -> tuple[str | None, str | None]:
        """返回 (中文, 引擎名)；两个引擎都失败时返回 (None, None)。"""
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            return None, None
        for name, fetch in (("google", self._google), ("mymemory", self._mymemory)):
            if self.requests_made >= self.max_requests:
                log.debug("翻译请求已达上限 %d，剩余简介保留原文", self.max_requests)
                return None, None
            self.requests_made += 1
            try:
                result = fetch(text)
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("%s 引擎失败：%s", name, exc)
                if self._note_failure():
                    return None, None
                continue
            if result:
                self._consecutive_failures = 0
                return result, name
            if self._note_failure():
                return None, None
        return None, None


def translate_texts(
    texts: Iterable[str],
    *,
    translator: Translator,
    cache: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[tuple[str, str, str]], TranslationStats]:
    """批量把简介译成中文。

    返回 (原文→中文, 需要写入缓存的新条目 [(原文, 中文, 引擎)], 统计)。
    需要翻译但没译出来的简介不会出现在第一条里，调用方据此回退原文。
    """
    known = cache or {}
    mapping: dict[str, str] = {}
    fresh: list[tuple[str, str, str]] = []
    stats = TranslationStats()
    seen: set[str] = set()

    for raw in texts:
        if not needs_translation(raw):
            continue
        text = raw.strip()
        if text in seen:
            continue
        seen.add(text)

        cached = known.get(source_hash(text))
        if cached:
            mapping[text] = cached
            stats.from_cache += 1
            stats.translated += 1
            continue

        zh, engine = translator.translate(text)
        if not zh:
            stats.failed += 1
            continue
        mapping[text] = zh
        fresh.append((text, zh, engine or "unknown"))
        stats.translated += 1

    return mapping, fresh, stats
