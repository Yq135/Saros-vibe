# Saros 智能知识管理系统 — 实施计划

> 导出时间：2026-08-14。状态：需求已讨论，计划已确认方向，待用户补充确认后实施。

## Context

从零搭建个人知识管理系统 Saros（仓库现仅 README + .gitignore）。核心理念「沉淀即永恒」：联网获取的新知识与用户手动沉淀的旧总结碰撞、复用。四大模块：

1. **联网问答**：提问 → 免费搜索源联网检索 → LLM 合成带引用回答 + 自动标签，历史入库
2. **网页出题**：传 URL → 抽取正文 → LLM 生成 3-5 个「读后掌握」问题
3. **B站视频**：传 B站链接 → 下载字幕/音频/视频 → 提炼带时间点大纲（可点击跳转播放）→ 出题
4. **知识沉淀**：手动输入知识点+标签（**输入框禁止粘贴**，强制手打加深记忆）→ 入库存 embedding → 后续问答自动关联沉淀知识

**已确认的决策**：本地 Web 应用（无登录单用户）｜DeepSeek 等国产模型（OpenAI 兼容可配）｜免费搜索源｜程序内自建 B站管线｜无字幕时音频模型兜底（**不做转写**）｜向量检索+标签混合关联｜Python FastAPI + Vue 3｜**PostgreSQL + pgvector 存储（PG 在用户本地 Linux 已搭建）**｜**B站 cookie 由用户提供**｜**首期范围：先完成模块 1+4 核心闭环**，再加 2、3

环境已核实：Python 3.10 / Node 23 / ffmpeg 已装，无额外系统依赖。

## 技术选型

| 用途 | 选择 | 说明 |
|---|---|---|
| 后端 | FastAPI + uvicorn + pydantic-settings | 配置走 `.env` |
| LLM | `openai` SDK，base_url 指向 DeepSeek 等 | `LLM_BASE_URL/API_KEY/MODEL` 可配，便于切换国产模型 |
| 数据库 | **PostgreSQL + pgvector** | 元数据与向量同库（2026-08-14 用户确认，替代 SQLite+sqlite-vec）；PG 在用户本地 Linux 已搭建、pgvector 扩展已启用（512 维）；连接走 .env；schema 脚本建表（不用 alembic）；抽象 VectorStore 接口 |
| 嵌入 | sentence-transformers + BAAI/bge-small-zh-v1.5（512 维） | 本地 CPU 推理；查询侧加 BGE 前缀；国内设 `HF_ENDPOINT=https://hf-mirror.com` |
| 搜索 | `ddgs`（DuckDuckGo）主力，Bing/百度抓取兜底 | 接口化可插拔，全部免费 |
| 网页抽取 | trafilatura | 失败兜底 Jina Reader（`r.jina.ai/{url}`，免费） |
| 视频下载 | yt-dlp（Python API + progress_hooks） | 字幕优先 json3，srt/vtt 兜底解析 |
| 音频理解 | **通义 Qwen-Omni**（DashScope OpenAI 兼容接口，已确认） | 无 CC/AI 字幕时启用：音频直接生成大纲/问题，**不做转写** |
| 前端 | Vue 3 + Vite + vue-router + Element Plus | marked + DOMPurify 渲染 Markdown；composable 管状态（不用 pinia） |

## 项目结构

```
backend/
  requirements.txt  .env.example  # 依赖 + 配置样例（PG_*、LLM_*、AUDIO_LLM_*、EMBEDDING_MODEL、SEARCH_PROVIDERS、COOKIE_PATH）
  app/
    main.py            # 装配、CORS、启动时加载嵌入模型 + 视频任务清扫
    config.py db.py schemas.py llm.py embeddings.py
    vector_store.py    # pgvector 封装（upsert/delete/KNN+标签过滤）
    search.py          # SearchProvider 接口 + DDG/Bing/百度实现
    prompts.py         # 全部中文 Prompt 模板
    routers/           # qa.py webpages.py videos.py knowledge.py
    services/          # qa_service.py extractor.py video_jobs.py
                       # bilibili.py audio_analyzer.py chunking.py outline.py
  tests/               # 单测 + TestClient 集成 + @pytest.mark.live
  data/                # gitignore：cookies.txt + media/{bvid}/
frontend/
  src/views/           # QaView WebpageView VideoView VideoDetailView KnowledgeView SettingsView
  src/components/      # MarkdownView TimestampLink PasteGuardTextarea TagChips SourceList
  src/composables/useVideoJobs.js   # 轮询
dev.sh                 # 一键起前后端
```

## 数据模型（关键表）

- `tags`(name UNIQUE) — 问答/网页/知识点**共用标签空间**，利于知识碰撞
- `knowledge_points`（含 pgvector 向量列，id 与向量一一对应）+ `knowledge_point_tags`
- `qa_history`（question, answer, sources_json, retrieved_kp_ids, 标签关联表）
- `webpages` + `webpage_questions`（题干 + reference_answer 参考答案）
- `video_jobs`（status/progress/step/error/三件套路径/outline_md）+ `video_segments`（带 start_ts/end_ts 的字幕段，音频模式无此数据）+ `video_questions`（带 ts 可跳转）

## API 概要（前缀 /api）

- **问答**：`POST /api/qa/ask` → **SSE 流**（`start` 先回来源+引用的沉淀 id → `delta` 流式答案 → `done` 含完整答案+标签）；`GET /api/qa/history`（标签/关键词筛选）、`GET/DELETE /api/qa/{id}`
- **网页**：`POST /api/webpages`（同步，约 20-40s）→ 列表/详情/删除
- **B站**：`POST /api/videos` → `202 {job_id}`；`GET /api/videos/jobs/{id}`（前端 2s 轮询进度）、`/result`（大纲/题目/字幕段）、`/retry`（断点续跑）、DELETE；`GET /media/{bvid}/...` 文件服务（支持 HTTP Range，video 拖动可用）
- **沉淀**：`POST /api/knowledge`（建标签→嵌入→写向量）、列表（标签/关键词筛选）、`PUT/DELETE /api/knowledge/{id}`、`GET /api/tags?q=`（自动补全）
- **设置**：`GET/PUT /api/config`（LLM 配置，提示重启生效）

## 核心流程

### 混合 RAG 检索（模块 4 → 模块 1）

`POST /qa/ask` 执行链：联网搜索（DDG→Bing 兜底，合并去重 top 8-10）→ 问题嵌入 KNN 取 50 候选 → 标签命中（jieba 分词匹配标签名）+ 关键词重叠打分 → **混合打分 `0.6*cosine + 0.3*lex_overlap + 0.15*tag_hit`**，阈值 0.35 取 top 5（全低于阈值则不带沉淀，避免噪音）→ 合成 Prompt（沉淀知识权威性高于搜索结果，冲突以沉淀为准并说明；引用 [n] 标注；资料不足明说）→ 流式输出 → 结束后一次轻量调用生成 3-5 个中文标签 → 入库（记录 retrieved_kp_ids 可追溯）

### B站 Pipeline（模块 3，状态落库为唯一真相源）

```
queued → downloading(0-60) → [audio_fallback(60-75,无CC/AI字幕时)] → outlining(75-90) → questions(90-98) → completed(100)
任意步失败 → failed（可 retry，已有文件跳过=断点续跑）
```
- 并发限制：`asyncio.Semaphore(1)` 同时只跑一个视频任务；重活走 `to_thread`
- 步骤：URL 校验（BV 号正则 + b23.tv 解析，非 B站 400）→ yt-dlp 下载（**用户提供的 cookie**、720p 封顶可配、字幕 json3 优先、progress_hooks 报进度）→ ffmpeg 提音频（视频下载失败降级仅音频+字幕）→ 字幕段归一化入 video_segments → **无 CC/AI 字幕时走音频模式**：下载最低分辨率视频（或仅音频）→ ffmpeg 提音频 → 按约 5 分钟切片 → 多模态音频模型逐片生成主题级大纲（时间戳为切片起点的**粗粒度**锚点，不转写）→ 有字幕时：**分块 ≤6000 字/块（≈8-9k token，DeepSeek 64K 安全余量）**，块间重叠 200 字 → Map 逐块生成带 `[MM:SS]` 锚点大纲（**强制只能用本块已有时间戳，防幻觉**）→ Reduce 合并去重 → 基于大纲出 5-8 题（带 ts 可跳转）
- 大纲定位：**大致框架**（哪个时间点讲什么主题），非逐句笔记
- 启动清扫：中间态任务置 failed（"服务重启中断"），retry 续跑
- 音频模式失败（音频不可得/音频模型调用失败）→ failed，error 注明原因
- LLM 容错：JSON 解析失败重试 1 次；超时 300s 重试 1 次

### 禁粘贴（模块 4）

`PasteGuardTextarea.vue` 三层拦截：`@paste.prevent`（主拦截）+ keydown 拦截 Ctrl/Cmd+V（IME 时序兜底）+ `@drop.prevent`（挡拖拽），触发 `ElMessage.warning("为加深记忆，请手动输入，粘贴已被禁止")`。UI 注明这是自我约束工具（IME 联想等无法拦截），非安全机制。

## 分阶段实施（按用户选定的顺序）

| 阶段 | 内容 | 里程碑 |
|---|---|---|
| M0 脚手架 | 后端 FastAPI 骨架 + 前端 Vite/Element Plus/路由/代理 + .gitignore + dev.sh | 前后端同起，导航壳可见 |
| M1 模块 4 | PG/pgvector 建表 + 嵌入；knowledge/tags CRUD；KnowledgeView（禁粘贴） | 手录知识点→打标签→筛选正常，嵌入就绪 |
| M2 模块 1 | search.py；混合检索 + SSE 流式 + 标签；QaView + 历史 | **1+4 核心闭环达成**：提问→联网回答带引用→关联沉淀→历史可查 |
| M3 模块 2 | trafilatura + Jina 兜底抽取；webpages API；WebpageView | URL→正文+3-5 题+标签入库 |
| M4 模块 3 | 下载/cookie/字幕/媒体服务/详情页 → 音频模型兜底 → 大纲+出题 → retry/清扫打磨 | 链接→归档三件套→带时间戳大纲→点击跳转播放 |
| M5 打磨 | 设置页、README 使用说明、补齐测试 | 可日常使用 |

## 验证

- **单测（无网络）**：chunking 边界、时间戳解析、字幕格式解析器、URL 校验（mock）、混合打分、任务状态机（FakeExecutor）、Prompt 渲染
- **集成（TestClient + 本地 PG 测试库）**：knowledge CRUD + 真实嵌入（session 级 fixture）+ KNN 检索正确性；qa_service 注入 FakeSearch/FakeLLM 断言 RAG 注入与标签入库
- **live 标记**（默认 skip）：真实 DeepSeek 调用、真实 ddgs、真实 yt-dlp 短视频
- **手动 E2E**：录知识点（验证粘贴被拦）→ 问相关问题（观察引用沉淀区）→ 网页出题 → B站视频（有字幕视频 + 无字幕短视频走音频模式，观察进度流转、时间戳点击跳转、删除清理、中断重启 retry 续跑）

## 风险与注意

- 免费搜索源可能限流/不稳定 → 多 provider 兜底 + 搜索全挂但有沉淀时降级仅基于沉淀回答并注明
- 嵌入模型首次下载需网络，国内设 HF 镜像
- 音频模式需独立的 DashScope（Qwen-Omni）API key，产生少量费用
- PG 部署在用户本地 Linux（pgvector 已启用），需网络可达
- yt-dlp 下载 403 → 使用用户提供的 cookie；cookie 失效时明确提示用户更新
- DeepSeek 单次输出默认 4K 上限，大纲/出题需注意 max_tokens
