# AI 作业草稿：基本信息与文件分析（第二部分）

本模块复用 `0018_assignment_generation_orchestration` 的 job、revision、stage、snapshot、issue、ProviderInvocation 与晚到丢弃机制，只把 `analyzing` 和 `processing_pages` 两个阶段从占位实现扩展为可审计草稿分析。作业始终保持 `draft`；Worker 和 Provider 没有发布入口。

## 范围与数据模型

- `AssignmentFieldSuggestion`：按 revision、字段和 suggestion version 保存建议、规范值、置信度、证据、教师 disposition 与教师编辑版本。Provider 输出不直接写 `Assignment`。
- `AssignmentSourceFileAnalysis`：保存 source snapshot、MIME、checksum、页数、建议角色、建议答案来源、重复关系、风险以及教师确认。
- `AssignmentPageAnalysis`：引用现有 `PaperPage` 和最新 `PageProcessingResult`，投影空白、低质量、损坏、不支持、待转换、疑似缺页与 variant 风险；不实现第二套转换或图像质量算法。
- 所有教师接受、修改、拒绝、总分确认、文件角色和答案来源确认均写入既有 `AuditLog`。

迁移为 `0019_assignment_metadata_file_analysis`，直接继承 0018，新增三张表及稳定约束/索引；downgrade 按页面分析、文件分析、字段建议的外键逆序删除。

## 建议与教师确认边界

字段白名单为 `title`、`subject`、`grade`、`academic_year`、`assessment_type`、`description`、`instructions`、`total_score`。`class_ids` 与 `due_at` 不在 Provider schema 中；AI 不能选择班级，也不设置截止时间。无法判断时保存 `null`，不使用 0 或 100 作为哨兵。

普通字段只有教师 accept/modify 后才可写入仍为 draft 的 `Assignment`；写入同时检查 suggestion `teacher_edit_version` 与 `Assignment.updated_at`。`academic_year` 和 `assessment_type` 当前只保留在版本化建议中，因为正式 Assignment 尚无对应产品字段。总分使用独立的 `explicit_confirmation=true` 接口，不能走普通接受。

文件角色枚举：`question_paper`、`reference_answer`、`rubric`、`instructions`、`attachment`、`unknown`。

答案来源枚举：`teacher_official`、`publisher_official`、`teacher_provided`、`third_party`、`ai_generated`、`unknown`、`not_applicable`。答案文件必须由教师确认来源；非答案文件必须为 `not_applicable`。AI、第三方或未知来源建议不能被确认成官方答案。

## Provider、安全与失效

Provider 支持 `unavailable` 和仅测试环境可用的 deterministic fake；production 请求 fake 会降级为 unavailable。结构输出使用禁止额外字段、长度/枚举/置信度/证据约束的 Pydantic schema。Schema 无效时不写业务建议，只记录脱敏 invocation 和稳定 issue，不向 API 泄露原始异常或凭据。

文件名与 OCR 文字均作为不可信数据。系统 prompt、文档内容和结构输出边界分离；Provider 没有工具、数据库或发布权限。检测“忽略系统要求”“自动发布”“选择班级”等典型文本时，只产生 `PROMPT_INJECTION_CONTENT_DETECTED` 和脱敏证据摘要，不执行文档中的指令；前端只以 React 纯文本渲染，不使用 `dangerouslySetInnerHTML`。

写入前继续执行 0018 的 generation、cancel、snapshot、stale 和 `teacher_edit_version` 护栏。上传文件 checksum、页面序号/旋转/状态或 Provider 配置变化会使旧 revision/分析不可确认。阶段重试追加 stage generation；未审查结果可 supersede，已审查结果保留，Worker 不原地覆盖教师确认。

## 风险与页面分析

继续复用 `GenerationIssue`。本模块生成低置信度、总分未确认/冲突、文件角色、答案来源、精确/可能重复、空白、低质量、损坏、不支持、待转换、疑似缺页、多 variant、混合文档、Prompt Injection、Provider unavailable 与人工复核风险。答案来源未确认、总分冲突、主要文件损坏/不支持、疑似缺页、多 variant 和 stale 为 blocking；高置信度本身不会消除 blocking issue。

精确重复基于 checksum；可能重复只给建议且不删除文件。缺页仅在已有 `source_page_number` 序列不连续时标记 suspected，不伪造确定结论。A/B 卷只产生文件/页面级 variant suspicion，不拆题。

## API

- `GET /api/assignment-draft-revisions/{revision_id}/field-suggestions`
- `PATCH /api/assignment-field-suggestions/{suggestion_id}/disposition`
- `POST /api/assignment-field-suggestions/{suggestion_id}/confirm-total-score`
- `GET /api/assignment-draft-revisions/{revision_id}/file-analyses`
- `GET /api/assignment-source-file-analyses/{analysis_id}`
- `PATCH /api/assignment-source-file-analyses/{analysis_id}/confirmation`
- `GET /api/assignment-source-file-analyses/{analysis_id}/pages`
- `GET /api/assignment-page-analyses/{analysis_id}`

所有接口按 owner 隔离；所有写操作只接受教师会话并带乐观锁版本。

## 前端与恢复

既有六步向导和 0018 生成面板保持不变形，只扩展第一步/第二步审查区。界面显示当前建议、confidence、evidence、disposition、文件元数据、角色/答案来源确认、重复和页面风险，并明确班级/截止时间/总分/文件角色边界。刷新时从 revision API 恢复字段 disposition、文件确认、issues 和版本；网络失败不会清空后端确认。

## 未建立能力

- 未实现真实题目边界、题号、题干、题型、知识点抽取。
- 未实现真实答案或 Rubric 生成。
- 未实现集中审查或自动发布。
- 未接入真实 Provider、真实 API Key 或真实材料质量集。
- AI 不能自动选择班级；AI 不设置截止时间。
- 总分、文件角色和答案来源必须由教师确认。
- AI/第三方答案不是官方答案。

**REAL-PROVIDER QUALITY PENDING**

## 验证结果（2026-07-26）

- 后端新增聚焦测试：4 passed；覆盖 deterministic fake、draft-only、字段 disposition、总分显式确认、重复文件、第三方答案边界和 0019 往返迁移。
- 后端完整 pytest：202 passed、2 skipped；只有既有 Starlette/Pillow 弃用警告。
- Ruff 全仓检查通过；mypy 83 个 source files 无问题。
- 前端聚焦 Vitest：9 passed；完整 Vitest：49 passed。
- Prettier、ESLint、TypeScript typecheck 通过。
- Next 15 production build 成功，17 个静态页面完成；保留既有 lockfile SWC 自动修补失败但不阻断构建的警告。
- Alembic 唯一 head 为 0019；0019 SQLite 自身 upgrade/downgrade/upgrade 测试通过；0018→0019 upgrade 与 0019→0018 downgrade 离线 SQL 均成功生成。

这些确定性 fixture 只证明流程与安全边界，不证明真实模型质量。
