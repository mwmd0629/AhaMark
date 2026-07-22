# AhaMark 能力与证据矩阵

基线日期：2026-07-22。状态针对本表所列能力的完整范围，不把“有代码”或“测试通过”自动提升为真实环境或生产可用。

## 状态定义

- `IMPLEMENTED_AND_VERIFIED`：已实现，且有可定位的真实环境、真实 HTTP 或浏览器证据；只对证据覆盖的窄范围成立。
- `IMPLEMENTED_AUTOMATED_ONLY`：已实现，但只有单元/集成自动化或 TestClient 证据。
- `PARTIAL`：部分实现或部分验证，仍有重要缺口。
- `NOT_RUN`：已有实现/方案，但关键验证没有运行。
- `UNAVAILABLE`：没有可用正式实现或 Provider。
- `OUT_OF_SCOPE`：当前版本明确不做。

真实证据简称：`HTTP72` = `analytics72-http-verification.json`；`BROWSER72` = `analytics72-browser-smoke.json`；`PERF` = `performance-results.json`；`DOC-EVIDENCE` = HANDOFF/FINAL-ACCEPTANCE 中记录但本次未重跑的环境验证。

## 摘要

| 模块 | 状态 | 结论 |
|---|---|---|
| 认证 | IMPLEMENTED_AND_VERIFIED | 真实登录/CSRF 会话用于 HTTP72 与 BROWSER72；多实例限速等仍有限制 |
| 班级与学生 | PARTIAL | CRUD/导入自动化；真实环境只覆盖列表读取 |
| 作业和 Rubric | PARTIAL | CRUD/发布自动化；真实环境只覆盖列表/详情读取 |
| 试卷 OCR | PARTIAL | RapidOCR 印刷体窄范围真实验证；公式/手写/DOCX 有缺口 |
| 学生作业 OCR | IMPLEMENTED_AUTOMATED_ONLY | Submission 专用链路和持久化有自动化，无可定位真实异步闭环证据 |
| 客观题评分 | IMPLEMENTED_AUTOMATED_ONLY | 确定性规范化精确匹配有自动化 |
| 主观题评分 | UNAVAILABLE | 真实 Provider 不存在，必须人工评分 |
| 教师复核 | IMPLEMENTED_AUTOMATED_ONLY | 复核、修改、Revision、finalize guard 有自动化 |
| 最终成绩 | IMPLEMENTED_AUTOMATED_ONLY | 唯一来源不变量已实现并测试，未真实走通完整 finalize 链路 |
| GradeRelease | IMPLEMENTED_AUTOMATED_ONLY | 固定 Snapshot 已实现，学生送达 OUT_OF_SCOPE |
| XLSX/PDF | IMPLEMENTED_AND_VERIFIED | 格式自动化及既有 Celery/MinIO 冒烟；容量仍未验证 |
| Analytics | IMPLEMENTED_AND_VERIFIED | HTTP72 + BROWSER72 覆盖的范围通过 |
| TeachingInsight | IMPLEMENTED_AND_VERIFIED | 规则型建议生命周期经 HTTP/浏览器验证 |
| 文件安全 | PARTIAL | 统一检查与部分 fixture 自动化；完整恶意样本未跑 |
| 权限隔离 | PARTIAL | Analytics 14 项真实拒绝、文件 3 类自动化；完整矩阵缺失 |
| 性能 | PARTIAL | 单客户端同步接口冒烟；并发/异步/资源未测 |
| 备份恢复 | PARTIAL | PostgreSQL 既有恢复记录；MinIO 未运行 |
| 生产部署 | UNAVAILABLE | 当前仅等级 C，开发 Compose 不能直接生产使用 |

## 逐项证据

### 1. 认证 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/auth.py`、`apps/api/app/api/actor.py`、`apps/api/app/models.py`、前端 login/AuthGate。
- 模型/迁移：User、UserSession；`0001_initial`、`0008_teacher_sessions`。
- 自动化证据：`tests/test_auth.py` 覆盖登录、me、CSRF、退出、过期、production 禁 demo、教师隔离和初始化教师。
- 真实证据：HTTP72 两个教师真实登录；BROWSER72 登录与换教师；DOC-EVIDENCE 记录 Nginx 下登录/CSRF/Cookie 检查。
- 限制：限速是单进程内存；Cookie 重放、多会话 UI、完整固定会话测试未完成；当前在线状态未复核。
- 可以说：数据库会话、scrypt、HttpOnly/SameSite、写请求 CSRF、production Secure/禁 demo 已实现，并在开发验证范围内通过。
- 禁止说：企业级 IAM、完整 SSO/MFA、多实例防爆破或生产认证已验收。
- 后续验收条件：完整会话攻击用例、多副本 Redis 限速、全资源权限矩阵和生产 TLS/secret 配置。

### 2. 班级与学生 — `PARTIAL`

- 实现位置：`apps/api/app/api/domain.py`，前端 classes 页面，CSV/XLSX 两阶段导入。
- 模型/迁移：Class、Student、ClassStudent、Group、ImportJob/Row/Error；`0002_classes_students_imports`。
- 自动化证据：`tests/test_classes.py` 覆盖 CRUD、归档、成员、分组、CSV/XLSX preview/confirm/idempotency；前端 classes tests。
- 真实证据：PERF 仅覆盖班级列表和 50 人学生列表；未覆盖真实 CRUD/导入浏览器闭环。
- 限制：完整跨教师 create/update/archive/import 矩阵未跑；真实学生数据禁止使用。
- 可以说：教师端班级/学生管理和合成数据导入流程已实现。
- 禁止说：已通过真实学校数据验证、可做正式学籍管理。
- 后续验收条件：合成数据真实 HTTP/浏览器 CRUD、导入失败/幂等、跨教师全动作验证。

### 3. 作业和 Rubric — `PARTIAL`

- 实现位置：`apps/api/app/api/assignments.py`、AssignmentWizard、作业/编辑/Rubric 页面。
- 模型/迁移：Assignment、PaperVersion、Question/Region、RubricVersion/Item、KnowledgePoint；`0003`、`0005`。
- 自动化证据：`tests/test_assignments.py` 覆盖 CRUD、文件/页面/题目/区域、Rubric、发布检查、拒绝上传。
- 真实证据：PERF 只验证作业列表和 20 题详情读取。
- 限制：完整教师浏览器创建至发布闭环未运行；未知分值必须阻止 Rubric/发布。
- 可以说：作业、题目、版本化 Rubric 与发布前完整性检查已实现。
- 禁止说：完整作业制作流程已做真实 E2E 验收。
- 后续验收条件：真实 HTTP/浏览器覆盖创建、文件、题区、Rubric、发布及错误路径。

### 4. 试卷 OCR — `PARTIAL`

- 实现位置：`apps/api/app/recognition/pipeline.py`、`apps/api/app/api/recognition.py`、RecognitionWorkspace、OCR Worker。
- 模型/迁移：RecognitionJob、PageProcessingResult、RecognitionBlock、Candidate/Correction；`0003`、`0004`。
- 自动化证据：`tests/test_recognition.py` 覆盖转换/预处理、fake/unavailable、RapidOCR 小型印刷体、Block 持久化。
- 真实证据：DOC-EVIDENCE 记录本地 RapidOCR 3.9.2 小样本与 Celery/MinIO 冒烟；未形成独立 HTTP/浏览器产物。
- 限制：样本极小、无 CER/WER；手写数学未经充分验证；公式 Provider unavailable；LaTeX 不可靠；DOCX 缺 LibreOffice 时 unavailable；fake 仅测试。
- 可以说：本地印刷体文字 OCR 输出文本、坐标、置信度，支持状态与人工修正。
- 禁止说：可靠公式/手写识别、高准确率、适合真实答卷自动评分。
- 后续验收条件：可审计真实异步流程、代表性合成样本、错误/重试、准确率口径；公式/手写需独立 Provider 与证据。

### 5. 学生作业 OCR — `IMPLEMENTED_AUTOMATED_ONLY`

- 实现位置：`apps/api/app/recognition/submission.py`、`apps/api/app/api/grading.py`、Submission OCR Worker。
- 模型/迁移：SubmissionRecognitionJob/Block、SubmissionPage、StudentAnswer/Region；`0006`、`0009`。
- 自动化证据：`tests/test_submission_workflow.py` 覆盖 Worker 幂等、答案写入、页面结构与 finalize guard。
- 真实证据：无可定位的学生作业 OCR 真实 HTTP/浏览器闭环；既有文档也仅明确自动化链路。
- 限制：多页归并、匹配异常、低置信、公式、重试和 Celery/MinIO 组合未形成完整真实证据。
- 可以说：学生作业 OCR 工程链路已实现并有自动化测试。
- 禁止说：学生答卷异步 OCR 已在真实环境完整验收。
- 后续验收条件：纯合成 PDF/图片经上传、匹配、异步 OCR、修正、重试到答案持久化的可审计验证。

### 6. 客观题评分 — `IMPLEMENTED_AUTOMATED_ONLY`

- 实现位置：`apps/api/app/grading/providers.py`、`apps/api/app/api/grading.py`。
- 模型/迁移：GradingJob/Result、StudentAnswer、Rubric；`0006`。
- 自动化证据：`tests/test_grading.py` 验证大小写/空格规范化精确匹配；submission workflow 覆盖后续复核。
- 真实证据：无独立真实 HTTP/浏览器评分闭环。
- 限制：只适用于答案及可接受答案明确的题型；单位、精度、等价表达、歧义和 OCR 异常需人工复核。
- 可以说：明确答案客观题可由确定性规则给出初批建议。
- 禁止说：语义等价自动判断、所有客观题无需人工复核。
- 后续验收条件：合成答案矩阵、边界输入、OCR 修正后重算及复核闭环真实验证。

### 7. 主观题评分 — `UNAVAILABLE`

- 实现位置：`apps/api/app/grading/providers.py` 中接口、UnavailableProvider 和测试专用 FakeGradingProvider。
- 模型/迁移：可保存建议/证据/复核的 Grading 模型；`0006`，但无正式 AI Provider。
- 自动化证据：unavailable 返回 `score=null`；production 配 fake 会降级 unavailable。
- 真实证据：无；Fake 结果不得算证据。
- 限制：所有主观题必须教师人工评分。
- 可以说：已预留 Provider 接口，并安全降级为人工评分。
- 禁止说：主观题 AI 已可用、准确率、全自动主观题批改。
- 后续验收条件：正式 Provider、安全/隐私设计、数据集与评价协议、人工复核门槛和真实环境证据；在此前保持 unavailable。

### 8. 教师复核 — `IMPLEMENTED_AUTOMATED_ONLY`

- 实现位置：`apps/api/app/api/grading.py`、review 页面。
- 模型/迁移：TeacherReview、ScoreRevision、GradingEvidence/CriterionResult；`0006`。
- 自动化证据：复核决策、手动分数、批量资格、一致性、修改留痕、finalize guard。
- 真实证据：无完整浏览器复核闭环。
- 限制：低置信、异常、主观题、`score=null`、修正答案和 Rubric 变化必须复核；不能无条件批量接受。
- 可以说：教师可接受、修改、拒绝、手动评分并保留修订记录。
- 禁止说：AI 建议会自动成为最终成绩。
- 后续验收条件：浏览器逐题/批量资格、并发修改、审计与失败恢复验证。

### 9. 最终成绩 — `IMPLEMENTED_AUTOMATED_ONLY`

- 实现位置：`apps/api/app/results/services.py` 的 FinalScoreService、`apps/api/app/api/grading.py` 的 `POST /submissions/{id}/finalize`。
- 模型/迁移：SubmissionScoreSnapshot、Submission.finalized；`0006`、`0007` details schema。
- 自动化证据：snapshot schema、分值范围、重复题目、合计、finalize guard 和模型分层测试。
- 真实证据：Analytics 的 seeded complete Snapshot 被读取，但没有从上传/评分/复核到 finalize 的完整真实链路证据。
- 限制：仅 finalized + 最新 complete；旧 schema 或不完整 details 拒绝；缺失不是零分。
- 可以说：正式成绩来源规则已实现并由自动化保护。
- 禁止说：GradingResult/建议分/临时复核是正式成绩，或未完成等于零分。
- 后续验收条件：合成数据真实 HTTP 从复核到多版本 Snapshot、旧/不完整 Snapshot 拒绝及审计验证。

### 10. GradeRelease — `IMPLEMENTED_AUTOMATED_ONLY`

- 实现位置：`apps/api/app/api/results.py`、`apps/api/app/results/services.py`。
- 模型/迁移：GradeRelease、GradeReleaseItem.score_snapshot_id；`0007`。
- 自动化证据：Release 从 FinalScoreService 建项、固定 Snapshot、报告/Analytics 服务读取固定 Release。
- 真实证据：HTTP72 使用已 seed 的固定 Release，但未真实创建发布；无学生端或通知。
- 限制：`released` 仅为教师确认数据；后续改分需新 Snapshot 与新 Release 版本。
- 可以说：发布版本固定具体成绩快照，可追溯且旧版不变。
- 禁止说：学生已收到、学生端已上线或已通知送达。
- 后续验收条件：真实创建/取消/多版本/改分后新发布验证；学生送达保持 OUT_OF_SCOPE。

### 11. XLSX/PDF — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/results/services.py`、`apps/api/app/results/jobs.py`、Report Worker；仓库内 Noto Sans SC。
- 模型/迁移：ReportJob、StoredFile、ReportJobStudentScope；`0007`、`0010`。
- 自动化证据：XLSX sheet/文本学号/公式防护，中文 PDF 字体与解析，Worker 只收 Job ID 且幂等。
- 真实证据：DOC-EVIDENCE 记录真实 Celery/MinIO 报告冒烟；HTTP72 验证报告列表与失败任务 retry 创建新 Job。
- 限制：本轮未重跑；30–50 份 PDF、ZIP、大文件、到期下载与并发容量未验证。
- 可以说：固定 GradeRelease 的真实 XLSX/中文 PDF 生成链路在开发环境做过冒烟。
- 禁止说：大批量报告容量或生产下载链路已验收。
- 后续验收条件：可重复异步容量、对象恢复、到期 URL、失败重试与敏感导出控制验证。

### 12. Analytics — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/results/services.py`、`apps/api/app/api/results.py`、Analytics 页面与学生详情。
- 模型/迁移：AnalyticsSnapshot 固定 GradeRelease；`0007`。
- 自动化证据：metrics 公式、分布、错误、知识点、主观题不宣称正确率、分页稳定性和前端状态。
- 真实证据：HTTP72 覆盖四类下钻、三类趋势、学生详情、缺交不记零及错误输入；BROWSER72 覆盖加载、下钻和趋势。
- 限制：证据基于小型固定合成数据；未覆盖全业务、并发、大数据量或教学效果。
- 可以说：固定发布版本的统计、下钻和趋势已在合成开发环境真实验证。
- 禁止说：AI 学情诊断、真实教学效果、生产规模分析。
- 后续验收条件：保持计算口径回归，并对更大合成规模、并发、查询数与所有页面状态验证。

### 13. TeachingInsight — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/results.py`、Analytics 页面。
- 模型/迁移：TeachingInsight.provider=`rule_based`，固定 AnalyticsSnapshot；`0007`。
- 自动化证据：生成内容只引用 metrics，确认时重校 evidence，状态与编辑规则有代码/测试支撑。
- 真实证据：HTTP72 验证生成、编辑、确认、重新生成、失效与跨教师隐藏；BROWSER72 验证编辑确认。
- 限制：固定规则和小样本，不是大模型或自主诊断；教师必须审阅。
- 可以说：规则型教学建议可编辑、确认、重新生成和失效。
- 禁止说：真实 AI 深度分析、大模型自主教学诊断。
- 后续验收条件：规则版本、解释性、边界样本和教师确认审计持续回归。

### 14. 文件安全 — `PARTIAL`

- 实现位置：`apps/api/app/security/files.py`、各上传入口、通用文件路由。
- 模型/迁移：StoredFile.owner/status；`0001`。
- 自动化证据：假 PDF、扩展名/MIME 不一致、超像素、ZIP 穿越、路径文件名和跨教师文件路由拒绝。
- 真实证据：DOC-EVIDENCE 记录 Nginx/签名 URL/上传加固的局部验证；完整 fixture 无证据。
- 限制：加密/超页 PDF、异常 EXIF、宏、外链、ZIP 条目/极端压缩比、真实 URL 到期为 NOT RUN。
- 可以说：已实现统一内容检查和若干关键回归。
- 禁止说：恶意文件专项完整通过、文件上传生产安全。
- 后续验收条件：完整运行时 fixture 矩阵、资源限制、代理限制、对象回滚和签名 URL 到期验证。

### 15. 权限隔离 — `PARTIAL`

- 实现位置：CurrentActor、各 owned 查询、StoredFile owner 校验。
- 模型/迁移：主要资源 owner_id/关联所有权散布于 0001–0010。
- 自动化证据：认证教师隔离、文件 3 类跨教师 404、多个业务测试。
- 真实证据：HTTP72 记录 Analytics/学生/报告/Insight 共 14 项 Teacher B 隐藏。
- 限制：Class 至 TeachingInsight 的 list/get/create/update/archive/retry/download/confirm/finalize 完整矩阵未执行。
- 可以说：Analytics 范围有真实跨教师拒绝，通用文件有自动化 owner 回归。
- 禁止说：全平台租户隔离已完整验收。
- 后续验收条件：按资源×动作建立完整矩阵，含错误信息不泄漏、间接 ID 与签名 URL。

### 16. 性能 — `PARTIAL`

- 实现位置：`scripts/performance_smoke.py`、`PERFORMANCE.md`。
- 模型/迁移：不适用；fixture 使用合成 2×50 人、2×20 题。
- 自动化证据：无独立性能门禁。
- 真实证据：PERF 中五类同步接口 100% 成功，单客户端 P95 40.51–88.18 ms。
- 限制：非并发；未测 OCR/报告/Analytics 异步吞吐、150–250 页、队列等待、SQL 数、CPU/内存和慢查询。
- 可以说：开发环境单客户端同步接口延迟冒烟通过。
- 禁止说：支持 50 人并发、容量达标、生产性能通过。
- 后续验收条件：只有在单独授权的后续验收中补齐并发、异步和资源指标；本基线不实施。

### 17. 备份恢复 — `PARTIAL`

- 实现位置：`OPERATIONS.md`、只读 `scan_storage_orphans`。
- 模型/迁移：全库和 StoredFile 对象引用；无自动恢复模型。
- 自动化证据：孤儿扫描工具只读设计；无完整灾备自动化。
- 真实证据：DOC-EVIDENCE 记录独立 PostgreSQL 逻辑恢复及关键表计数；MinIO 恢复 NOT RUN。
- 限制：数据库与对象必须成对备份；Redis/MinIO 中断、版本化、RPO/RTO 和恢复后一致性未验证。
- 可以说：PostgreSQL 在独立环境做过一次逻辑恢复验证，且有只读孤儿扫描。
- 禁止说：完整灾备通过、MinIO 可恢复、已满足 RPO/RTO。
- 后续验收条件：受控目标上的 PostgreSQL+MinIO 联合恢复、引用一致性、权限与恢复演练记录。

### 18. 生产部署 — `UNAVAILABLE`

- 实现位置：开发 Compose、可选 Nginx 配置、Dockerfile、OPERATIONS。
- 模型/迁移：PostgreSQL 目标与 0010 head；不构成生产部署证据。
- 自动化证据：Compose/build/配置在既有文档中有检查记录。
- 真实证据：DOC-EVIDENCE 记录本地六服务和 Nginx 冒烟，仅为开发环境。
- 限制：当前 Compose 暴露端口；无正式 TLS、secret store、监控告警、完整容量/安全/灾备和发布审计；仓库无提交。
- 可以说：提供开发部署骨架和本地代理配置。
- 禁止说：生产可用、可公网开放、可真实教学试点。
- 后续验收条件：首先需要用户批准建立可追溯 Git 基线；生产能力仍需独立完整验收，当前保持 unavailable。

## 明确 OUT_OF_SCOPE

学生端、成绩通知/送达、公共注册、全自动主观题批改和大模型自主教学诊断不属于当前版本。GradeRelease 的存在不得改变这些范围判断。
