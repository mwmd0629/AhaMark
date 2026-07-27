# AI 作业草稿：集中审查、版本绑定与教师发布（第五部分）

本部分建立在 0018–0021 的生成、文件分析、题目抽取、答案和 Structured Rubric 草稿之上，新增集中人工审查与两阶段发布。范围止于发布 Assignment；不发布学生成绩，不创建 `GradeRelease` 或 `ScoreSnapshot`，不运行 AI 最终评分。

## 发布依赖与显式边界

既有发布依赖为 `Assignment → active PaperVersion → Question → active legacy RubricVersion → QuestionRubric/RubricItem → publish`；生成依赖为 `GenerationJob → DraftRevision → Question → ReferenceAnswerVersion → StructuredRubricVersion → RubricCriterion`。

两条链只在教师点击“准备发布评分标准”时由单一集中审查服务连接。Worker 不双写 legacy Rubric；发布链不在两套 Rubric 间隐式选择。每次转换创建新的 draft `RubricVersion`，保留 Structured/Answer ID、内容哈希、来源、criterion 映射、分值和转换警告；confirmed binding 不原地修改。dependency、alternative path 或 validation rule 无法无损表达时生成 warning，并阻止确认，直到教师修改源 Rubric。

## 0022 模型

- `AssignmentReviewSession` 固定 owner、assignment、generation、DraftRevision、PaperVersion、source snapshot、乐观并发版本及风险账本。部分唯一索引保证一个 Assignment 同时最多一个活动会话。
- `AssignmentReviewItem` 保存服务器生成的 section、实体、风险等级、证据、source hash 和教师 disposition。相同 source hash 刷新保持 disposition；变化后的历史项标记 stale，不删除审计历史。
- `AssignmentExplicitConfirmation` 独立记录 classes、due_at、total_score、file_roles、answer_sources、paper_version、reference_answers、structured_rubrics 和 legacy_binding。当前值重新哈希不匹配时确认自动失效。
- `AssignmentRubricPublicationBinding` 保存 Structured 到 legacy 的确定性映射与版本；相同 source binding hash 幂等，不覆盖旧 legacy Rubric。
- `AssignmentPublishReadinessSnapshot` 保存短期、一次性、服务器哈希的不可变发布输入。发布时重新计算并逐项比对。

迁移 `0022_assignment_central_review_publish` 以 `0021_assignment_answer_rubric_generation` 为父版本，upgrade/downgrade 对称，所有历史记录外键使用 `RESTRICT`，避免发布审计被级联删除。

## 红黄绿风险

红色包括班级、截止时间、总分、文件角色、答案来源、当前 Paper/Answer/Structured Rubric、分值一致性、generation/revision/source stale、legacy binding 和显式确认缺失。红色不能 acknowledge，必须通过修改真实来源或服务器认可的人工解决动作清零。

黄色包括 AI/第三方答案以及 Structured 语义无法无损投影的 dependency、alternative path 和 validation rule；必须逐项查看并 acknowledge。绿色为服务器判定的完整信息项，仍不代表自动确认或自动发布。

风险账本采用稳定排序、稳定 JSON 序列化和 SHA-256。客户端不能上传任意 Issue、风险计数或哈希。

## 两阶段教师发布

第一阶段 `POST /api/assignment-review-sessions/{id}/prepare-publication` 要求教师会话、CSRF、expected review version 和 `explicit_confirmation=true`。服务在事务中重新生成风险，要求 blocking/warning 均为零且全部显式确认有效，然后创建 15 分钟有效的 readiness snapshot；Assignment 仍保持 draft，不派发 Worker。

第二阶段 `POST /api/assignments/{id}/publish` 强制携带 readiness ID/hash、expected Assignment updated_at 和 `explicit_confirmation=true`。事务锁定并重新核对 Assignment、session、Paper、binding、legacy Rubric、generation/revision、risk/source/state hash、classes、due_at 和 total_score，再运行既有 `publish_issues`。成功后原子设置 Assignment published/published_at、消费 readiness、标记 session published 并写一次 AuditLog。相同已消费 readiness 的重复请求返回同一已完成结果，不重复发布或记录业务事件。

任何 Assignment、班级、文件、页面、题目、答案、Rubric、generation、DraftRevision、Issue 或确认来源变化都会使哈希不一致，prepare/publish 将拒绝旧输入。owner 查询统一同时过滤 owner；其他教师得到 404。写 API 由既有浏览器教师 session 和 CSRF 依赖保护。

## API

- Review：创建/列出/get/refresh session，列出 item，更新 disposition。
- Confirmation：`confirm/classes|due_at|total_score|file_roles|answer_sources|paper-version|reference-answers|structured-rubrics`。
- Binding：创建/get binding，确认 binding。
- Readiness：prepare publication，get readiness。
- Publish：现有 publish API 改为强制 readiness 请求体。

列表 limit 为 1–100、稳定排序；写操作记录 AuditLog。响应不包含 Cookie、CSRF、数据库 URL、对象存储内部键或秘密。

## 前端第六步

第六步改为“集中审查与发布”，展示 generation、DraftRevision、PaperVersion、legacy binding、session 状态及红黄绿计数；支持风险/section 过滤、证据、跳转修改、黄色 acknowledge、八类独立确认、准备/确认 binding、准备 readiness 和带班级/截止/总分/版本摘要的“教师确认并发布”二次确认。页面加载只恢复已有 session，不创建 readiness，不自动发布；没有 `dangerouslySetInnerHTML`。

## AI/Worker 安全边界

AI 不自动选择班级，不确认截止时间或总分。AI/第三方答案不是官方答案；AI 不自动确认 Paper/Answer/Rubric，不创建教师 confirmation、binding 或 readiness，不消费 readiness，不写 `Assignment.status=published`/`published_at`。assignment generation Worker 不导入集中发布服务，能力声明保持 `publishes_assignment: false`。发布只能由带有效 session 和 CSRF 的教师浏览器请求触发。

发布 Assignment 不等于发布学生成绩。本部分不创建 Submission、评分任务、`ScoreSnapshot`、`GradeRelease`，不写 `TeacherReview.final_score`，不通知学生。

## 验证与限制

2026-07-26 已通过数据库隔离守卫 `18 passed, 1 skipped`、0022 upgrade/downgrade/upgrade 与 PostgreSQL 双向离线 SQL `2 passed`，以及绿色端到端教师审查/绑定/readiness/发布/重复提交 `4 passed`。后端完整 pytest 为 `244 passed, 3 skipped, 37 warnings`；前端完整 Vitest 为 `52 passed`。Ruff format/check、mypy（75 个源文件）、Prettier、ESLint、TypeScript 和 Next production build 均通过。Next 仍输出既有 SWC lockfile 自动修补失败警告，但编译、类型检查、17 个静态页面和构建进程成功。

每轮 pytest 前后事故库保持 SHA-256 `2F7CC45C46BFBDDF5A2348959F50DD00385AC36D2DC9498DD33D60855E1D8F22`、2,158,592 bytes、UTC mtime ticks `639206399433661871`，WAL/SHM/journal 均不存在。测试只使用带 marker 的系统临时 SQLite。

本部分不证明真实 Provider 或真实材料质量，不是第六部分预生产验收。

**REAL-PROVIDER QUALITY PENDING**

**AFFECTED DATABASE RECOVERY NOT PERFORMED**
