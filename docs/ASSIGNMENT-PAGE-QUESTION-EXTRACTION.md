# AI 作业草稿：页面整理与题目抽取（第三部分）

本部分建立在 0018 编排和 0019 基本信息/文件分析之上，把 `extracting_questions` 从占位阶段改为可审计的草稿流程。Assignment 始终为 `draft`，Provider 与 Worker 没有确认或发布权限。

## 复用与数据模型

继续复用 `PaperVersion`、`PaperPage`、`PageProcessingResult`、`RecognitionJob`、`RecognitionBlock`、`QuestionCandidate`、`QuestionCandidateRegion`、`Question`、`QuestionRegion`、`KnowledgePoint` 和既有存储/证据访问，不增加 PDF 渲染、裁切、去噪或签名 URL 流水线。

迁移 `0020_assignment_question_extraction` 新增：

- `PaperPageOrganizationSuggestion`：保存页面顺序、旋转、状态、重复/variant、置信度和证据建议；建议不会直接修改 `PaperPage`。
- `AssignmentQuestionExtractionCandidate`：按 generation/revision/candidate version 保存父子关系、原始 Recognition 来源、题号（字符串）、题型、题干、可空 LaTeX/分值、难度/知识点建议、字段级 confidence/evidence、warning、教师 disposition 与物化 ID。
- `AssignmentQuestionExtractionRegion`：按 display order 保存题号、题干、分值、公式、图形、表格等归一化区域；同一候选可以引用一页多个区域或多页区域。

既有 `QuestionCandidate` 是 Recognition 的临时候选，缺少草稿 revision、教师 edit version、父子关系和字段级证据，因此未直接改变其生命周期。新的编排候选通过 `source_recognition_job_id` 和 `source_question_candidate_id` 保留来源关系，不构成第二套 OCR 系统。

## 页面整理与抽取边界

只有 0019 中经教师确认且角色为 `question_paper` 的文件可成为来源。未确认角色、其他角色、被排除页、stale Recognition 或未完成页面处理会产生 Issue 并停止业务候选落库。原始文件、图像和 `source_page_number` 不被修改。空白页、exact duplicate、旋转与排序也只生成建议，教师 accept/modify 后才写 draft PaperPage；probable duplicate、低质量、有内容的页面、variant 和跨页后续页必须人工复核。

一页多题由不同候选/区域表示；一题跨页由同一候选的多个 `paper_page_id` 和 display order 表示；同题多区域保留 region type，不合并成大矩形。父子题使用候选自引用，并校验同 revision/PaperVersion、自环和循环。

字段级 confidence/evidence 覆盖题号、父子关系、题型、题干、LaTeX、分值、难度、知识点和 regions。分值无法从明确文字或教师输入得到时保持 `null`，不从总分反推或平均分配。

## 教师确认、物化与并发

页面和题目 disposition 请求携带 suggestion/candidate teacher edit version、draft revision edit version、PaperVersion 与 source snapshot。事务锁定候选、revision 和目标页面/版本；并发操作至多一个成功。accept/modify 后才写入 draft `Question`/`QuestionRegion`，`materialized_question_id` 保证重复接受不会重复创建。已确认 PaperVersion 不原地修改；当前 API 要求先派生 draft PaperVersion。所有写操作进入 `AuditLog`。

批量确认资格完全由服务器判断：当前、非 stale、高置信度、有题号/题干/明确分值、高置信度区域、父子关系合法且无 variant/结构冲突；proof、公式、图形、表格、跨页、多区域阅读冲突、分值缺失/冲突均不 eligible。

## Worker、Provider 与安全

Worker 在落库前后沿用 0018 generation、snapshot、cancel、draft edit version 和 late-result 检查。重试追加 stage generation，旧 suggested 结果 superseded，不覆盖 accepted/modified/rejected/manual_required 或已物化候选。

第三部分 Provider schema 使用 Pydantic `extra=forbid`，限制候选数量、字符、枚举、分值、confidence、坐标、page、parent、Block 和 evidence 引用，并拒绝 owner、assignment、confirmed、published 与工具调用字段。fake 只用于非 production 测试，production 自动降级 unavailable；unavailable 不伪造题目。试卷文字明确作为 `untrusted_document_content`，典型 Prompt Injection 只产生脱敏 Issue并保留合法题干，不改变状态机或置信度。

复杂公式无法验证时 `content_latex=null`；proof、复杂公式、figure、table 强制人工复核。Provider 无数据库、班级、账号、发布或工具调用权限。

## API

- `GET /api/assignment-draft-revisions/{id}/page-organization-suggestions`
- `PATCH /api/page-organization-suggestions/{id}/disposition`
- `GET /api/assignment-draft-revisions/{id}/question-extraction-candidates`
- `GET /api/question-extraction-candidates/{id}`
- `GET /api/question-extraction-candidates/{id}/evidence`
- `PATCH /api/question-extraction-candidates/{id}/disposition`
- `POST /api/assignment-draft-revisions/{id}/question-extraction-candidates/accept-eligible`

列表限制为 1–100，稳定排序并按 owner 隔离。前端扩展第三步页面建议和第四步题目编辑，纯文本渲染字段、风险、字段级 confidence/evidence 和所有 regions，保留原有手动题目/区域工作区；不会自动接受、物化、跳转或发布。

## 测试与已知限制

聚焦测试覆盖严格 schema、坐标/parent、null 分值、formula/proof、服务器 eligible、Prompt Injection 与 0020 upgrade/downgrade/upgrade。2026-07-26 最终验证：后端 `210 passed, 2 skipped`（37 条既有依赖弃用 warning）；0020 聚焦 `8 passed`；Ruff 121 files、mypy 85 source files 通过；前端 Vitest `50 passed`，Prettier、ESLint、TypeScript 和 Next production build 通过。0020 是唯一 Alembic head，自身 SQLite upgrade/downgrade/upgrade 和 PostgreSQL 方言双向离线 SQL 生成均通过。完整历史链的通用 `alembic --sql` 仍受历史 0011 迁移运行时 inspector 限制，因此 PostgreSQL 离线结论只声明 0020 自身，不冒充全历史离线能力。

本部分未实现标准答案生成、Rubric 生成、AI 学生答案评分或集中审查发布；尚未接入真实多模态题目抽取 Provider，真实材料识别质量也未评测。公式 Provider 不可用时不生成 LaTeX；proof、复杂公式、图形和表格必须人工复核。AI 不能自动确认题目，AI/Worker 不能发布。

**REAL-PROVIDER QUALITY PENDING**
