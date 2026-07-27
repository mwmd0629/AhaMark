# AI 作业草稿：标准答案与 Structured Rubric（第四部分）

本部分建立在 0018–0020 已验收编排、文件分析和题目抽取之上，把 `generating_rubrics` 与 `validating` 从占位阶段扩展为版本化答案/Rubric 候选、结构风险验证和教师 disposition。范围止于未确认的正式 draft；第五部分集中审查/发布和第六部分真实 Provider 质量验收未实现。

## 架构选择与职责边界

现有 `ReferenceAnswerVersion` 和 `StructuredRubricVersion` 继续作为唯一正式答案/Structured Rubric 版本系统，但不直接保存 AI 未审查输出。原因是正式 Structured Rubric 要求正数总分、非空 criteria，并且既有创建 API 绑定已确认答案；分值未知、Provider unavailable、Schema 非法和 manual-only 输出不能安全塞入正式模型。

迁移 `0021_assignment_answer_rubric_generation` 因此新增编排域 Candidate：

- `AssignmentAnswerDraftCandidate`：保存 raw/normalized/teacher value、结构化答案、替代答案、真实来源、provenance、confidence/evidence/warning、教师版本和幂等物化 ID。
- `AssignmentRubricDraftCandidate`：保存 scoring mode、可空总分、数域/单位/精度/格式、验证配置、常见错误、反馈模板和教师版本。
- `AssignmentRubricCriterionDraft`：保存稳定 key/order、分值、必要性、依赖、替代路径组、部分分、扣分、验证规则和人工边界。
- `AssignmentRubricValidationResult`：汇总结构与确定性能力路由，状态只使用 `verified/partially_verified/indeterminate/unsupported/failed/stale`。

教师 accept/modify 后才物化新的 draft `ReferenceAnswerVersion` 或 draft `StructuredRubricVersion`/`RubricCriterion`。confirmed 正式版本不原地修改；后续修改通过新的 version 派生。

legacy `RubricVersion/QuestionRubric/RubricItem` 仍承担当前 Assignment 发布门禁、评分版本和成绩 Snapshot 的业务职责。第四部分不双写 legacy Rubric，也不改变 `Assignment.active_rubric_version_id`，避免一次教师操作产生两套可漂移的正式内容。未来集中审查如需发布，必须以显式转换/对账把已确认 Structured Rubric 投影到 legacy 发布版本；该转换不属于本部分。

## 答案来源与 provenance

来源标签为 `teacher_official`、`publisher_official`、`teacher_provided`、`third_party`、`ai_generated` 和 `unknown`。所有 Provider 新生成答案强制为 `ai_generated`；Worker 不接受输出中的 owner、confirmed、published、final_score 或来源升级字段。`unknown` 阻止教师接受/物化；AI 和第三方来源即使被教师接受，标签仍不改变，也绝不显示成官方答案。

provenance 记录 source type、file/page/region（存在时）、generation job、Provider/model/config version、候选 ID、教师审查者和时间。ProviderInvocation 与 provenance 不保存 API Key、Cookie、CSRF、数据库 URL、对象存储 secret 或原始内部异常。

## 生成、替代答案与 Provider

只有当前 revision 中状态为 accepted/modified 且已有 `materialized_question_id` 的 active Question 才参与生成。答案候选保留原始文本、规范文本、严格结构化对象、多个替代答案和等价性状态；不能证明等价时保持 `indeterminate`。

Provider 接口支持 unavailable 与仅 test 环境可用的 deterministic fake；production 请求 fake 会降级 unavailable。unavailable 只创建 raw/normalized 为空的 `manual_required` 答案候选，不伪造答案、公式或 LaTeX。fake fixture 只证明流程、Schema 与确定性安全，不证明真实质量。未来 OpenAI-compatible Provider 只能接在相同严格 Pydantic `extra=forbid` 边界之后。

## Structured Rubric、依赖和部分分

服务器结构验证覆盖：

- criterion key/order 唯一；分值为 null 或非负；
- dependency 只能引用同一 candidate 且图无环；
- alternative group 按互斥路径取最大值，防止重复计分；
- 合法路径有效总分必须等于 Question.max_score；
- partial credit 上限不得超过 criterion 分值；
- deduction 上限不得让 criterion 低于零；
- validation answer type 必须在服务器白名单；
- manual-only 不能携带伪装成确定性覆盖的配置。

Question.max_score 为 null 时仍可保存 Candidate/criteria 的 null 分值，但产生 `RUBRIC_SCORE_REQUIRED`，不能物化正式 Rubric，更不能确认。

正式 `RubricCriterion.metadata` 保存 candidate 的 alternative group、deduction rule、common error codes、feedback template 和 scoring mode；partial credit 与 dependency 使用正式模型原生字段。

## Scoring mode 与数学验证边界

模式只能由服务器能力路由和教师显式修改决定：完整受支持配置可为 `deterministic`；混合人工 criteria 为 `hybrid`；建议型内容为 `ai_suggestion`；proof、Jordan、Smith、图形论证、开放答案或必要性/充分性无法确定时强制 `manual_only/manual_required`。

既有 math-validation 的能力白名单覆盖线性方程组、参数解集、子空间/基、多项式、特征多项式、最小多项式、特征值/向量/子空间及 AP=PD/PDP^-1。生成阶段不创建伪造的学生 Submission/StudentAnswer 来凑 `MathValidationJob` 外键，也不写学生分数；它验证结构和确定性配置，并在没有教师确认来源可证明答案正确性/等价性时返回 `indeterminate`。Jordan、Smith 和完整证明返回 `unsupported/manual_required`；解析失败只返回 `failed/indeterminate`。数学验证失败只形成草稿风险，不判定教师答案错误。

## Worker、stale、取消、重试与晚到保护

`generating_rubrics` 和 `validating` 继续复用 0018 的行锁、generation、source snapshot、cancel、stage generation、expected teacher edit version 和提交前二次检查。重试追加 StageResult；旧 suggested candidate 可 supersede，教师 accepted/modified/rejected/manual 内容和 confirmed 正式版本不覆盖。新 generation、source snapshot 变化、取消或教师 edit version 变化会让晚到结果 discarded/stale。

完整运行只进入 `review_required`；任一 Provider unavailable/阶段不可用进入 `partial`。Worker 不进入 ready，不写 `Assignment.status=published`，不调用发布 API，也不创建 GradingResult、ScoreSnapshot、GradeRelease 或最终成绩。

## 教师 disposition 与 API

答案操作：accept、modify、reject、mark_manual_required。Rubric 操作：accept、modify、reject、mark_manual_only。请求携带 candidate teacher edit version、draft revision edit version、question version 和 source snapshot；事务锁与版本比较保证并发操作至多一个成功。materialized ID 保证重复 accept 幂等。

API：

- `GET /api/assignment-draft-revisions/{id}/answer-draft-candidates`
- `GET /api/answer-draft-candidates/{id}` 与 `/evidence`
- `PATCH /api/answer-draft-candidates/{id}/disposition`
- `POST /api/assignment-draft-revisions/{id}/answer-draft-candidates/accept-eligible`
- `GET /api/assignment-draft-revisions/{id}/rubric-draft-candidates`
- `GET /api/rubric-draft-candidates/{id}` 与 `/validation`
- `PATCH /api/rubric-draft-candidates/{id}/disposition`
- `POST /api/assignment-draft-revisions/{id}/rubric-draft-candidates/accept-eligible`

全部 owner 隔离、列表 limit 1–100、稳定排序，写操作写 AuditLog。批量资格由服务器判断；批量接受不改变来源标签。

## 前端第五步

六步向导第五步新增三栏审查：左侧题目/答案/Rubric/风险/mode；中间答案、替代答案、来源、provenance/evidence/validation；右侧 criteria、points、dependency、alternative path、partial/deduction、数域/单位/精度/格式、common errors、feedback 和 Issue。支持显式 accept/modify/reject/manual、服务器 eligible 批量操作、刷新恢复和 409 并发提示。

所有不可信题目、答案和 Provider 内容由 React 以纯文本渲染，没有 `dangerouslySetInnerHTML`。AI/第三方/未知不显示“官方”；indeterminate 不显示成 verified；Provider unavailable 不显示成成功。

## Prompt Injection

系统指令、能力清单和 untrusted document content 分离。典型“忽略 Rubric”“给满分”“自动发布”文本只产生脱敏 `PROMPT_INJECTION_CONTENT_DETECTED`，不改变 answer source、confidence、scoring mode、状态机或人工确认门禁。Provider 无工具、数据库、发布和最终评分写权限；严格 Schema 拒绝额外/越权字段。

## 验证记录与数据库安全

最终验证：数据库隔离守卫 `18 passed, 1 skipped`；0021 migration/ORM/答案/Rubric 聚焦分别为 `2 passed` 与 `8 passed`；Structured Rubric、Math Validation、completion/stale 及 0018–0020 聚焦回归全部通过；后端完整 pytest `238 passed, 3 skipped, 37 warnings`。前端第四部分聚焦 `1 passed`，完整 Vitest `51 passed`；Ruff format/check、mypy（87 source files）、Prettier、ESLint、TypeScript 和 Next production build 均通过。Alembic 唯一 head 为 0021，0021 自身 SQLite upgrade/downgrade/upgrade、ORM 列一致性和 PostgreSQL 双向离线 SQL 通过。Next build 保留既有 SWC lockfile 自动修补失败的非致命警告，但编译、类型检查、17 个静态页面和构建进程成功。

每轮 pytest 前后均核对事故数据库：SHA-256 `2F7CC45C46BFBDDF5A2348959F50DD00385AC36D2DC9498DD33D60855E1D8F22`、2,158,592 bytes、UTC mtime ticks `639206399433661871`，且 WAL/SHM/journal 不存在。所有测试数据库均由 `tests/conftest.py` 和 `test_support/database_isolation.py` 在系统临时目录创建并带会话 marker。

## 明确限制

- AI/第三方答案不是官方答案。
- AI 不自动确认答案或 Rubric。
- proof、Jordan、Smith 等保持人工处理。
- indeterminate 不是 verified。
- AI 不写最终成绩，AI 不发布。
- 第五部分集中审查/正式发布转换尚未实现。
- **REAL-PROVIDER QUALITY PENDING**。
- **AFFECTED DATABASE RECOVERY NOT PERFORMED**。
