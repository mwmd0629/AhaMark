# AI 作业生成总任务与版本编排（第一部分）

本模块提供生成任务、阶段、草稿版本、风险与审计编排，并把通过严格 Schema 的 Provider 输出经服务端语义校验物化为候选草稿。任何 Worker 结果都只写入草稿；发布仍是教师在既有作业流程中的独立显式操作。

## 数据与版本边界

- `assignment_generation_jobs`：同一作业按 `generation` 递增；同一 owner 的幂等键唯一，并保存请求指纹。
- `assignment_draft_revisions`：每个 job 对应一个可审阅草稿，按 `revision` 递增并保留父版本。
- `generation_stage_results`：每阶段按 `stage_generation` 追加历史，重试不会覆盖旧结果。
- `generation_issues`：记录 info、warning、blocking 风险；阻断问题未解决时不能激活草稿。
- `assignment_generation_provider_invocations`：保存 Provider 调用元数据和散列，不保存凭据或向用户暴露原始异常。

活动状态为 `queued`、`analyzing`、`processing_pages`、`extracting_questions`、`generating_rubrics`、`validating`；数据库部分唯一索引保证同一作业至多一个活动 job。终态包括 `partial`、`review_required`、`ready`、`failed`、`cancelled`、`stale`。

## 并发与失效规则

- 创建 job 同时受 owner 级幂等唯一约束、作业 generation 唯一约束和活动 job 部分唯一索引保护。
- Worker 以行锁原子领取 `queued` job；重复 Celery 投递不会重复执行或追加阶段结果。
- 阶段重试先在同一事务预留新的 stage result，再调度 Worker；并发重试只有一个请求能成功。
- 教师 metadata PATCH 使用 `expected_teacher_edit_version` 和数据库条件更新，避免丢失更新。
- Worker 落草稿前再次锁定 job、revision 和 stage result，并以 edit version 条件写入；教师已修改、取消、输入快照变化或新 generation 出现时，晚到结果标记为 `discarded`。
- 输入快照只包含影响生成的作业字段、试卷版本、文件 checksum/状态、页面顺序/旋转/状态和 Provider/prompt/schema 配置版本；任务、issue、invocation 或普通更新时间不会自行改变快照。

取消 `queued`、`partial` 或 `failed` job 会立即进入 `cancelled`；运行中 job 记录取消请求，由 Worker 在下一写入边界原子观察。失败、取消和 stale 不伪装成 100% 成功进度。

## API

- `POST /api/assignments/{assignment_id}/generation-jobs`
- `GET /api/assignments/{assignment_id}/generation-jobs?limit=50`
- `GET /api/assignment-generation-jobs/{job_id}`
- `POST /api/assignment-generation-jobs/{job_id}/cancel`
- `POST /api/assignment-generation-jobs/{job_id}/retry-stage`
- `GET /api/assignments/{assignment_id}/draft-revisions?limit=50`
- `GET /api/assignment-draft-revisions/{revision_id}`
- `PATCH /api/assignment-draft-revisions/{revision_id}/metadata`
- `POST /api/assignment-draft-revisions/{revision_id}/activate`

所有查询和变更均按 owner 隔离。激活只允许最新 generation 且已进入可审阅状态、输入快照仍一致、没有开放 blocking issue 的草稿。激活不会改变作业发布状态、发布时间或发布版本。

## Provider 与前端

默认 Provider 为 `unavailable`。`fake` 仅用于测试；非测试环境完全以服务器配置选择 Provider，客户端传入值不能改变 Provider、endpoint 或 model。`openai_compatible` 仅在服务器具备完整配置时调用固定 Responses endpoint；`local_openai_compatible` 只允许显式列入 allowlist 的内网 HTTP 主机，并调用固定 Chat Completions JSON Schema endpoint；否则安全降级 unavailable。根 Compose 的 `local-ai` profile 可启动只读模型卷中的 llama.cpp/Qwen 服务，模型必须先由固定获取脚本完成大小与 SHA-256 校验。

默认安全开关为 `ASSIGNMENT_GENERATION_ENABLED=true`、`ASSIGNMENT_GENERATION_PROVIDER=unavailable`、`ASSIGNMENT_GENERATION_ALLOW_EXTERNAL_PROVIDER_REQUESTS=false`、`ASSIGNMENT_GENERATION_ALLOW_TEACHER_START=true`、`ASSIGNMENT_GENERATION_SUGGESTION_ONLY=true`、`ASSIGNMENT_GENERATION_REAL_PROVIDER_QUALITY_PASSED=false`。教师界面从只读 capability API 获取这些服务器状态；启动请求不发送 Provider、endpoint 或 model。生产配置拒绝 fake 和非 suggestion-only 模式；已发布作业在创建生成任务前即被拒绝。

真实调用的数据流为 Worker → stage executor → dispatcher → HTTP transport → 严格 Pydantic DTO → 服务端 ownership/evidence/capability 校验 → 事务 materializer → versioned draft candidate → stage result。Provider 不接触 Session 或 ORM，也不能创建正式答案、正式 Rubric、readiness、发布或成绩。五阶段分别物化字段建议、文件/页面分析与页面组织建议、题目候选/区域、答案候选、Rubric/criterion；validating 只读取已保存候选并写服务器验证结果。

每次真实调用记录 provider、model/model snapshot、endpoint mode、provider/prompt/schema version、stage generation、request/response hash、provider request ID、token、图片计数/字节、估算成本、retry count、状态与脱敏错误。0023 仅为补齐这些审计列，不改变 0018–0022 的业务语义。

教师端面板恢复 job/revision 历史，显示 generation、revision、快照、阶段尝试、风险和不可用状态；只在活动状态轮询，错误、终态或卸载时停止。面板提供启动、取消和合法的单阶段重试，但没有发布按钮，也不渲染服务端 HTML。

## 验收边界

自动化测试覆盖幂等、真实双线程并发创建/编辑、状态机、取消、stale、晚到结果、教师编辑保护、阶段重试、重复 Worker 投递、owner 隔离、快照输入、激活限制、无自动发布，以及 0018 在隔离临时数据库上的 upgrade/downgrade/upgrade。

2026-07-26 验收结果：后端完整 pytest `198 passed, 2 skipped`；项目配置范围 mypy `80 source files` 零问题；前端 Vitest `48 passed`；Prettier、ESLint、TypeScript typecheck 和 Next production build 均通过。0018 已在隔离 SQLite 中完成真实 upgrade/downgrade/upgrade，并完成 PostgreSQL 方言离线 upgrade/downgrade SQL 生成。完整历史链不支持 SQLite（0001 使用 PostgreSQL JSONB），因此未宣称在 SQLite 上完成全历史迁移。

**REAL-PROVIDER QUALITY PENDING**：mocked HTTP 的实现与安全集成通过不代表真实模型质量；没有安全凭据和独立真实 Run 时不得宣称真实 AI 生成能力可用。
