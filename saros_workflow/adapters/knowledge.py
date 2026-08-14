"""节点B：知识库检索适配器。
优先级：飞书多维表格(primary) → 本地 JSON(fallback)。
飞书需 lark-cli 已装且已授权；未就绪时自动回退本地，保证工作流不中断。

字段名策略：飞书多维表格的字段是用户自定义的，不写死。
优先用 config 的 field_map（逻辑字段 -> 真实列名）；未配置时按常见中英文候选名匹配；
仍取不到则把该记录的全部字段拼接，保证不丢信息。

飞书读取依赖 `lark-cli base +record-list`，返回 matrix 格式：
  data.fields = ["记录ID","类型",...]          # 字段名顺序
  data.data   = [["NO.001", null, ...], ...]  # 每行按字段顺序排布
「来源链接」等文本/url 字段会被飞书格式化为 markdown 链接 [url](url)，此处清洗为纯 url。
"""
import os
import re
import json
import shutil
import subprocess

# 逻辑字段的候选名（顺序即优先级），仅当 field_map 未指定时使用
CONTENT_CANDIDATES = ["内容", "原文", "提问", "问题", "Question", "Content", "question", "content", "正文"]
SUMMARY_CANDIDATES = ["AI 总结", "总结", "摘要", "Summary", "summary", "内容"]
TAGS_CANDIDATES = ["标签", "Tags", "tags", "标签云"]
SOURCE_CANDIDATES = ["来源链接", "来源", "出处", "Source", "source", "URL", "url"]
TYPE_CANDIDATES = ["类型", "分类", "Category", "category", "type"]
UPDATED_CANDIDATES = ["创建时间", "更新时间", "修改时间", "Created", "Updated", "created_at", "updated_at"]
ID_CANDIDATES = ["记录ID", "ID", "RecordID", "id", "record_id"]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _pick(fields, mapped_name, candidates):
    """按 field_map 指定名优先，否则按候选列表取第一个存在的值。"""
    if mapped_name and mapped_name in fields:
        return fields[mapped_name]
    for c in candidates:
        if c in fields:
            return fields[c]
    return None


def _clean_source(val):
    """飞书文本/url 字段返回可能是 markdown 链接 [text](url)，提取纯 url。"""
    if not isinstance(val, str):
        return val if val is not None else ""
    m = _MD_LINK.search(val)
    if m:
        text, url = m.group(1), m.group(2)
        return url if text == url else f"{text} ({url})"
    return val


def _clean_tags(val):
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, str):
        s = val.strip().strip("[]")
        return [x.strip() for x in s.split(",") if x.strip()]
    return [str(val)]


def _all_fields_text(fields):
    parts = []
    for k, v in fields.items():
        if v in (None, ""):
            continue
        val = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}: {val}")
    return "；".join(parts)


def _kb_local(path, top_k=5):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa
        return [], f"local_error:{e}"
    entries = data.get("entries", [])
    out = []
    for i, e in enumerate(entries[:top_k], 1):
        out.append({
            "idx": i, "id": e.get("id"), "type": e.get("type", ""),
            "title": e.get("title", ""), "content": e.get("content", e.get("title", "")),
            "summary": e.get("summary", ""), "tags": e.get("tags", []),
            "source": e.get("source", ""), "updated_at": e.get("updated_at", ""),
        })
    return out, "local_ok"


def _kb_feishu(config, top_k=5):
    """通过 lark-cli 读取飞书多维表格「Saros知识库」。
    依赖：lark-cli 已安装且已授权（auth login，用户身份）。"""
    if shutil.which("lark-cli") is None:
        return None, "lark_cli_missing"
    app_token = config.get("base_app_token")
    table_id = config.get("table_id")
    if not (app_token and table_id):
        return None, "feishu_unconfigured"

    # 字段映射（config 可配；未配则空串，走候选匹配）
    fm = config.get("field_map", {}) or {}
    c_content = fm.get("content", "")
    c_summary = fm.get("summary", "")
    c_tags = fm.get("tags", "")
    c_source = fm.get("source", "")
    c_type = fm.get("type", "")
    c_updated = fm.get("updated_at", "")
    c_id = fm.get("id", "")

    try:
        cmd = [
            "lark-cli", "base", "+record-list",
            "--base-token", app_token, "--table-id", table_id,
            "--as", "user", "--format", "json", "--limit", str(max(top_k, 20)),
        ]
        raw = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if raw.returncode != 0:
            return None, f"feishu_err:{raw.stderr[:200]}"
        d = json.loads(raw.stdout)
        if not d.get("ok"):
            return None, f"feishu_apierr:{d.get('error', {}).get('message')}"
        payload = d.get("data", {})
        field_names = payload.get("fields", [])
        rows = payload.get("data") or []
        out = []
        for i, row in enumerate(rows[:top_k], 1):
            # matrix 模式：row 是数组，按 field_names 顺序；也兼容 dict{fields:{}}
            if isinstance(row, list):
                fields = dict(zip(field_names, row))
            elif isinstance(row, dict):
                fields = row.get("fields", row)
            else:
                fields = {}
            content = _pick(fields, c_content, CONTENT_CANDIDATES)
            summary = _pick(fields, c_summary, SUMMARY_CANDIDATES)
            tags = _clean_tags(_pick(fields, c_tags, TAGS_CANDIDATES))
            source = _clean_source(_pick(fields, c_source, SOURCE_CANDIDATES))
            rtype = _pick(fields, c_type, TYPE_CANDIDATES)
            updated = _pick(fields, c_updated, UPDATED_CANDIDATES)
            rid = _pick(fields, c_id, ID_CANDIDATES)
            # summary 仍为空 → 用全部字段兜底，绝不留空
            if not summary:
                summary = _all_fields_text(fields)
            title = (content or summary or "(无标题)")[:60]
            out.append({
                "idx": i, "id": rid, "type": rtype,
                "title": title, "content": content, "summary": summary,
                "tags": tags, "source": source or "飞书多维表格", "updated_at": updated,
            })
        return out, "feishu_ok"
    except Exception as e:  # noqa
        return None, f"feishu_exc:{e}"


def run_knowledge(config, top_k=5):
    """primary=feishu_base，失败/为空则 fallback=local。返回 (结果列表, 状态)。"""
    primary = config.get("primary")
    if primary == "feishu_base":
        res, st = _kb_feishu(config.get("feishu", {}), top_k)
        if res is None:
            # 飞书不可用 → 回退本地
            fb = config.get("fallback")
            if fb == "local":
                lres, lst = _kb_local(config.get("local", {}).get("path", ""), top_k)
                return lres, f"feishu_failed({st})->local({lst})"
            return [], st
        if len(res) == 0:
            # 飞书接通但无沉淀 → 回退本地兜底并标注，避免工作流空洞
            lres, lst = _kb_local(config.get("local", {}).get("path", ""), top_k)
            return lres, f"feishu_ok_but_empty->local({lst})"
        return res, st
    if config.get("fallback") == "local":
        return _kb_local(config.get("local", {}).get("path", ""), top_k)
    return [], "no_source"
