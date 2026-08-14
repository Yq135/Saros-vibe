"""节点A：联网搜索适配器。支持必应(Bing Web Search API v7)与头条。
无 API key 时返回空结果并标记，交由上层降级（agent 桥接或本地兜底）。
"""
import os
import json
import urllib.parse
import urllib.request


def _http_get(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def search_bing(query, top_k=8, api_key=None):
    if not api_key:
        return [], "no_key"
    endpoint = "https://api.bing.microsoft.com/v7.0/search"
    params = urllib.parse.urlencode({"q": query, "count": top_k, "mkt": "zh-CN"})
    url = f"{endpoint}?{params}"
    try:
        raw = _http_get(url, {"Ocp-Apim-Subscription-Key": api_key})
        data = json.loads(raw)
        out = []
        for i, w in enumerate(data.get("webPages", {}).get("value", [])[:top_k], 1):
            out.append({
                "idx": i, "title": w.get("name", ""),
                "url": w.get("url", ""), "snippet": w.get("snippet", ""),
            })
        return out, "ok"
    except Exception as e:  # noqa
        return [], f"error:{e}"


def search_toutiao(query, top_k=8, api_key=None):
    # 头条搜索无官方免费 Web API；此处预留接口，优先用必应。
    # 若你有内部搜索网关，把实现补在这里即可。
    if not api_key:
        return [], "no_key"
    # TODO: 接入你的头条/内部搜索网关
    return [], "not_implemented"


def run_search(query, config, top_k=8):
    """按 providers 顺序尝试，返回 (结果列表, 状态说明)。"""
    providers = config.get("providers", ["bing"])
    for p in providers:
        if p == "bing":
            res, st = search_bing(query, top_k, config.get("bing_api_key"))
        elif p == "toutiao":
            res, st = search_toutiao(query, top_k, config.get("toutiao_api_key"))
        else:
            res, st = [], "unknown_provider"
        if res:
            return res, f"{p}:{st}"
    return [], "all_unavailable"
