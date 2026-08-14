#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Saros 知识沉淀 · 自动化工作流运行器
节点：A 联网搜索(top8) → B 知识库召回(top5) → C LLM 合成 → D 用户可见输出
用户可见输出严格遵循 prompts/saros_role.md（来自 saros_prompt.txt）：
  【Saros的解答】 + 【知识标签】(1-3 个)，禁用 AI 套话、温柔陪伴语气。

用法：
  python workflow.py --question "你的问题"
  python workflow.py --question "..." --demo      # 仅跑 A/B，C 交 agent 桥接
"""
import os
import sys
import json
import argparse
import urllib.request

try:
    import yaml
except ImportError:  # 兜底，避免依赖缺失导致整条流崩溃
    yaml = None

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    path = os.path.join(HERE, "config.yaml")
    if yaml is None:
        raise RuntimeError("缺少依赖 pyyaml：请先 `pip install pyyaml`")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 替换 ${ENV} 占位符
    def resolve(node):
        if isinstance(node, dict):
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        if isinstance(node, str) and node.startswith("${") and node.endswith("}"):
            return os.environ.get(node[2:-1], "")
        return node
    return resolve(cfg)


def fmt_list(items, kind):
    if not items:
        return "（无可用结果）"
    lines = []
    for it in items:
        if kind == "search":
            lines.append(f"[{kind}{it['idx']}] {it['title']}\n  {it['snippet']}\n  URL: {it['url']}")
        else:
            lines.append(f"[{kind}{it['idx']}] {it['title']}\n  {it['summary']}\n  来源: {it['source']} | 更新: {it['updated_at']}")
    return "\n".join(lines)


def build_prompt(question, search, knowledge):
    return f"""你是 Saros，一个温柔、有耐心、充满智慧的智能陪伴助手，像一位懂用户的知心朋友。
严格遵循角色规范：禁用「根据搜索结果/综上所述/首先其次最后」等 AI 套话；多用「呀/呢/哦」+ 少量 Emoji；先共情再解答；像朋友聊天。

用户问题：{question}

========== 搜索结果（联网 top8）==========
{fmt_list(search, '搜')}

========== 知识库沉淀（top5）==========
{fmt_list(knowledge, '沉')}

========== 任务 ==========
请综合上述两类来源生成回答，硬性要求：
1. 先共情接住用户情绪，再给信息。
2. 每条事实用 [搜n] / [沉n] 标注来源，文末列来源清单。
3. 搜索结果与沉淀冲突时，以沉淀为准并温和说明差异。
4. 资料不足时坦诚说明，不编造。
5. 温柔陪伴语气（呀/呢/哦 + 少量 Emoji），禁机械套话。

仅输出如下 JSON（不要多余文字，节点D 会渲染成【Saros的解答】/【知识标签】格式）：
{{"answer": "回答正文（含 [搜n]/[沉n] 来源标注，温柔口语化）", "summary": "一句话总结", "tags": ["标签1","标签2"]}}
"""


def llm_synthesize(prompt, cfg):
    """调用 OpenAI 兼容接口。无 key 时返回 None（触发 agent 桥接）。"""
    c = cfg.get("C_synthesize", {})
    key = c.get("api_key")
    if not key:
        return None
    base = c.get("base_url") or "https://api.openai.com/v1"
    model = c.get("model") or "gpt-4o-mini"
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是 Saros 知识沉淀的陪伴型知识助手。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
    }).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_llm_json(text):
    try:
        return json.loads(text)
    except Exception:
        # 容错：从文本里抠出第一个 {...}
        s = text.find("{"); e = text.rfind("}")
        if s != -1 and e != -1:
            return json.loads(text[s:e + 1])
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", required=True)
    ap.add_argument("--demo", action="store_true", help="只跑 A/B，C 交 agent 桥接")
    args = ap.parse_args()

    cfg = load_config()
    wf = cfg["workflow"]

    # 节点A
    a = wf["A_search"]
    search, st_a = __import__("adapters.search", fromlist=["run_search"]).run_search(
        args.question, a, a.get("top_k", 8))
    print(f"[节点A 搜索] 状态={st_a} 召回={len(search)} 条", file=sys.stderr)

    # 节点B
    b = wf["B_knowledge"]
    knowledge, st_b = __import__("adapters.knowledge", fromlist=["run_knowledge"]).run_knowledge(
        b, b.get("top_k", 5))
    print(f"[节点B 知识库] 状态={st_b} 召回={len(knowledge)} 条", file=sys.stderr)

    # 节点C + D
    prompt = build_prompt(args.question, search, knowledge)
    if args.demo:
        # 桥接模式：把素材交给 agent 合成
        print("\n=== BRIDGE: 节点C 所需素材（交 agent 合成）===\n")
        print(prompt)
        return

    text = llm_synthesize(prompt, wf)
    if text is None:
        print("\n[节点C] 未配置 LLM_API_KEY，进入 agent-bridge 模式。", file=sys.stderr)
        print("=== BRIDGE: 节点C 所需素材（交 agent 合成）===\n")
        print(prompt)
        return

    result = parse_llm_json(text)
    answer = result.get("answer", "")
    tags = result.get("tags", []) or []
    # 面向用户：严格按 saros_role.md 的 persona_blocks 格式输出
    print("\n【Saros的解答】：")
    print(answer)
    print("\n【知识标签】：")
    print("；".join(tags) if tags else "（暂无合适标签）")
    # 内部结构化 JSON（供飞书「Saros知识库」回写：内容/AI总结/标签/来源链接）
    print("\n=== 节点D 结构化输出（供飞书回写，内部使用）===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
