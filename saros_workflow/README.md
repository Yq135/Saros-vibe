# Saros 知识沉淀 · 自动化工作流

问答流水线：**A 联网搜索 → B 知识库召回 → C LLM 合成 → D 用户可见输出**，
全程严格遵循 `prompts/saros_role.md`（即 saros_prompt.txt）的角色与语气规范，知识库优先级高于搜索。

## 角色与语气规范（权威）
- **`prompts/saros_role.md`**：由项目内 `saros_prompt.txt` 固化的唯一角色/语气依据。节点C、agent 桥接、最终回复**必须严格参考**。
- 关键约束：禁用「根据搜索结果/综上所述/首先其次最后」等 AI 套话；多用「呀/呢/哦」+ 少量 Emoji；先共情再解答；输出严格为 `【Saros的解答】` + `【知识标签】`(1-3 个)。

## 目录
```
config.yaml            工作流配置（密钥走环境变量）
workflow.py            运行器（A→B→C→D 编排）
prompts/saros_role.md  角色/语气权威规范（来自 saros_prompt.txt）
prompts/synthesize.md  节点C 合成 Prompt（引用/冲突/不足/语气，引用 saros_role.md）
adapters/search.py     节点A：必应 / 头条搜索
adapters/knowledge.py  节点B：飞书多维表格(优先) / 本地(兜底)
knowledge/saros_kb.json 本地兜底知识库（飞书未接时使用）
```

## 快速开始
```bash
pip install pyyaml
python workflow.py --question "Saros 知识沉淀是做什么的"
```

## 三种运行模式
| 模式 | 触发条件 | 行为 |
|---|---|---|
| 全自动 | 配了 `LLM_API_KEY`（+搜索 key） | A/B/C/D 一气呵成，直接出 JSON |
| agent-bridge | 没配 `LLM_API_KEY` | 跑完 A/B 后把素材 dump 出来，由 WorkBuddy 用自身 LLM 合成并温柔呈现 |
| demo | `--demo` | 只跑 A/B，C 交 agent |

## 接入飞书多维表格（节点B 的「Saros知识库」）✅ 已接通
> 状态：2026-08-14 已装 `lark-cli` v1.0.87、完成飞书 OAuth 授权（用户身份：王泳清），
> 并定位到「Saros知识库」多维表格（`app_token=JAfxbHYHKaBp24swmP5cuCFSnje`，表名「知识库」，`table_id=tblq21sVt3djX7MA`）。
> 已用一条临时记录验证过完整读取链路（插入→矩阵格式读回→字段解析→删除），现已清空。

节点B 默认优先读飞书，飞书无沉淀时回退本地兜底。配置已写入 `config.yaml`：

```yaml
B_knowledge:
  primary: "feishu_base"
  feishu:
    base_app_token: "JAfxbHYHKaBp24swmP5cuCFSnje"   # 多维表格 app_token
    table_id: "tblq21sVt3djX7MA"                    # 表名：知识库
    field_map:                                       # 逻辑字段 -> 你表里的真实列名
      id: "记录ID"
      type: "类型"
      content: "内容"
      summary: "AI 总结"
      tags: "标签"
      source: "来源链接"
      updated_at: "创建时间"
```

**「Saros知识库」真实字段（已逐字对齐）：**
| 逻辑字段 | 多维表字段 | 类型 |
|---|---|---|
| 内容(原始/提问) | 内容 | 多行文本 |
| AI 总结 | AI 总结 | 多行文本 |
| 标签 | 标签 | 多选 |
| 来源链接 | 来源链接 | 文本(飞书存为 `[url](url)`，读取时自动清洗为纯 url) |
| 类型 | 类型 | 单选(问答沉淀/文章笔记/视频笔记) |
| 创建时间 | 创建时间 | 日期 |
| 主键 | 记录ID | 自动编号 |

**往里沉淀知识**：直接往「Saros知识库」多维表格加记录即可（节点D 的 JSON 可反向写入 `内容`/`AI 总结`/`标签`/`来源链接`）。
注意「类型」是单选，需先在表里预置选项（问答沉淀/文章笔记/视频笔记）才能写入该字段。

## 节点C 硬性规则（已写入 synthesize.md）
- 引用来源标注：`[搜n]` / `[沉n]`
- 冲突以沉淀为准，并温和提示差异
- 资料不足明说，不编造
- 温柔陪伴、像知心朋友

## 节点D 输出结构
```json
{ "answer": "回答正文（含来源标注）", "summary": "一句话总结", "tags": ["标签1","标签2","标签3"] }
```
