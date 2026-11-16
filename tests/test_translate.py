"""简介翻译的单元测试：判定、解析、缓存与回退，全部不联网。"""

from __future__ import annotations

import httpx

from star_daily.translate import (
    Translator,
    has_chinese,
    needs_translation,
    parse_google,
    parse_mymemory,
    source_hash,
    translate_texts,
)

GOOGLE_OK = [[["快速的", "A fast ", None, None, 10], ["客户端", "client", None, None, 3]]]


def _translator(handler, **kwargs) -> Translator:
    """用 MockTransport 顶掉真实网络，Translator 的其他逻辑保持原样。"""
    return Translator(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_has_chinese_detects_mixed_text():
    assert has_chinese("muse.ai 注册与邀请码兑换协议研究（纯 HTTP Python 实现）")
    assert has_chinese("纯中文简介")
    assert not has_chinese("A fast HTTP client")
    assert not has_chinese("")
    assert not has_chinese("12345 !!!")


def test_needs_translation_skips_chinese_and_symbols():
    assert needs_translation("A fast HTTP client")
    assert not needs_translation("纯中文简介")
    assert not needs_translation("muse.ai 注册与邀请码兑换协议研究")
    assert not needs_translation("🎉🔥")
    assert not needs_translation("   ")
    assert not needs_translation(None)


def test_parse_google_joins_segments():
    assert parse_google(GOOGLE_OK) == "快速的客户端"


def test_parse_google_rejects_garbage():
    assert parse_google(None) is None
    assert parse_google([]) is None
    assert parse_google({"a": 1}) is None
    assert parse_google([[["", "x"]]]) is None


def test_parse_mymemory_reads_response():
    payload = {"responseStatus": 200, "responseData": {"translatedText": "很快"}}
    assert parse_mymemory(payload) == "很快"
    assert parse_mymemory({"responseStatus": 403, "responseData": {"translatedText": "很快"}}) is None
    assert parse_mymemory({"responseData": {"translatedText": "MYMEMORY WARNING: usage limit"}}) is None
    assert parse_mymemory({}) is None


def test_translate_uses_free_google_endpoint_first():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json=GOOGLE_OK)

    with _translator(handler) as tr:
        assert tr.translate("A fast client") == ("快速的客户端", "google")
    assert hosts == ["translate.googleapis.com"]


def test_translate_falls_back_to_mymemory():
    def handler(request):
        if request.url.host == "translate.googleapis.com":
            return httpx.Response(429, text="too many requests")
        return httpx.Response(
            200, json={"responseStatus": 200, "responseData": {"translatedText": "很快"}}
        )

    with _translator(handler) as tr:
        assert tr.translate("fast") == ("很快", "mymemory")


def test_translate_gives_up_when_both_engines_fail():
    def handler(request):
        return httpx.Response(500, text="boom")

    with _translator(handler) as tr:
        assert tr.translate("fast") == (None, None)


def test_translate_respects_request_budget():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(500)

    with _translator(handler, max_requests=1) as tr:
        assert tr.translate("a") == (None, None)
        assert tr.translate("b") == (None, None)
    assert len(calls) == 1


def test_translate_texts_uses_cache_and_only_translates_new_text():
    requested = []

    def handler(request):
        requested.append(request.url.params.get("q"))
        return httpx.Response(200, json=[[["中文简介", "x"]]])

    cached_text = "Already cached"
    cache = {source_hash(cached_text): "缓存好的译文"}
    with _translator(handler) as tr:
        mapping, fresh, stats = translate_texts(
            [cached_text, "Brand new thing", "已经是中文", "Brand new thing"],
            translator=tr,
            cache=cache,
        )

    assert mapping[cached_text] == "缓存好的译文"
    assert mapping["Brand new thing"] == "中文简介"
    assert fresh == [("Brand new thing", "中文简介", "google")]
    assert (stats.translated, stats.from_cache, stats.failed) == (2, 1, 0)
    # 去重后只翻一条，中文原文和缓存命中都不发请求
    assert requested == ["Brand new thing"]


def test_translate_texts_counts_failures_for_fallback():
    def handler(request):
        return httpx.Response(500)

    with _translator(handler) as tr:
        mapping, fresh, stats = translate_texts(["Boom"], translator=tr, cache=None)

    assert mapping == {} and fresh == []
    assert (stats.translated, stats.failed) == (0, 1)


def test_translate_stops_after_repeated_failures():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(500, text="boom")

    with _translator(handler) as tr:
        for text in ("a", "b", "c", "d", "e"):
            assert tr.translate(text) == (None, None)

    # a 试两个引擎共 2 次、b 再失败 1 次就达熔断阈值，之后一行简介都不再发请求
    assert len(calls) == 3

