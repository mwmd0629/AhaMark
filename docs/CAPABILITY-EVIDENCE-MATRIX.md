# AhaMark 能力与证据矩阵

基线日期：2026-07-28；Git 功能基线 `8746e18`。状态针对本表所列能力的完整范围，不把“有代码”或“测试通过”自动提升为真实环境或生产可用。

## 状态定义

- `IMPLEMENTED_AND_VERIFIED`：已实现，且有可定位的真实环境、真实 HTTP 或浏览器证据；只对证据覆盖的窄范围成立。
- `IMPLEMENTED_AUTOMATED_ONLY`：已实现，但只有单元/集成自动化或 TestClient 证据。
- `PARTIAL`：部分实现或部分验证，仍有重要缺口。
- `NOT_RUN`：已有实现/方案，但关键验证没有运行。
- `UNAVAILABLE`：没有可用正式实现或 Provider。
- `OUT_OF_SCOPE`：当前版本明确不做。

真实证据简称：`BUSINESS-E2E` = `business-e2e-verification.json` + `BUSINESS-E2E.md`；`HTTP72` = `analytics72-http-verification.json`；`BROWSER72` = `analytics72-browser-smoke.json`；`PERF` = `performance-results.json`；`AUTH-MATRIX` = `AUTHORIZATION-MATRIX.md` + `authorization-matrix-verification.json` + `authorization-http-verification.json`；`FILE-MATRIX` = `FILE-SECURITY-VERIFICATION.md` + `file-security-verification.json`；`RECOVERY7` = `backup-restore-verification.json` + `failure-recovery-verification.json` + 两份恢复手册；`DOC-EVIDENCE` = HANDOFF/FINAL-ACCEPTANCE 中记录的环境验证。

## 摘要

| 模块 | 状态 | 结论 |
|---|---|---|
| 认证 | IMPLEMENTED_AND_VERIFIED | 真实登录/CSRF 会话用于 HTTP72 与 BROWSER72；Redis 共享限速已在本地双 API 环境验证 |
| 班级与学生 | IMPLEMENTED_AND_VERIFIED | BUSINESS-E2E 覆盖真实浏览器创建、CSV 预览/确认、前导零与列表 |
| 作业和 Rubric | IMPLEMENTED_AND_VERIFIED | BUSINESS-E2E 覆盖六步向导、题目/题区/知识点/Rubric/发布；`4c6266b` 修复真实满分、manual-only 绑定和集中审查过滤 |
| 试卷 OCR | PARTIAL | Fake 编排 150/200/250 页、真实 RapidOCR 清晰印刷体 100/150/250 页完成；准确率、公式/手写/DOCX 仍有缺口 |
| 学生作业 OCR | IMPLEMENTED_AND_VERIFIED | BUSINESS-E2E 在隔离 test-only Fake OCR 下验证异步 UI/持久化编排，不证明真实 OCR 能力 |
| 客观题评分 | IMPLEMENTED_AND_VERIFIED | BUSINESS-E2E 显示 objective-rule Criterion/Evidence 并由教师接受 |
| 主观题评分 | UNAVAILABLE | 真实 Provider 不存在，必须人工评分 |
| 教师复核 | IMPLEMENTED_AND_VERIFIED | BUSINESS-E2E 覆盖客观题接受、主观题 UI 人工评分、强制项与 finalize |
| 最终成绩 | IMPLEMENTED_AND_VERIFIED | BUSINESS-E2E + 第四部分金标准 v1/v2，合法 complete Snapshot 与缺失不记零通过 |
| GradeRelease | IMPLEMENTED_AND_VERIFIED | 第四部分验证 v1/v2 固定具体 Snapshot 且历史不漂移；学生送达 OUT_OF_SCOPE |
| XLSX/PDF | IMPLEMENTED_AND_VERIFIED | 固定 Release 的 50 名不同学生、50 PDF、50 行 XLSX、50 PDF ZIP；52/52 Job completed；`8746e18` 覆盖所有外部文本列公式注入防护 |
| Analytics | IMPLEMENTED_AND_VERIFIED | 50/100/200 人、20/50/100 题及同 Release 顺序/20 路并发幂等通过；最大规模学生读取约 8 秒 |
| TeachingInsight | IMPLEMENTED_AND_VERIFIED | 第四部分验证规则型建议 evidence 固定 AnalyticsSnapshot 及历史不漂移 |
| 文件安全 | IMPLEMENTED_AND_VERIFIED | 41/41 结构 fixture、故障补偿及真实 URL 到期通过 |
| 权限隔离 | IMPLEMENTED_AND_VERIFIED | 27×29、702/702、全路由边界及 HTTP 16/16 通过 |
| 性能 | PARTIAL | 第六部分开发机有界容量 PASS；PARTIAL 仅指生产容量、SLA、多实例与故障条件尚未建立 |
| 备份恢复 | IMPLEMENTED_AND_VERIFIED（开发范围） | RECOVERY7：PostgreSQL/MinIO 独立恢复及单 Worker 故障恢复通过；生产灾备、高可用和 RPO/RTO 未建立 |
| 生产灾备/RPO/RTO | UNAVAILABLE | 异地、加密、增量、长期、生产规模和正式恢复目标均未建立 |
| 生产高可用/多实例恢复 | UNAVAILABLE | 第八部分仅验证本地双 API 切换；PostgreSQL、Redis、MinIO、Nginx 和 Worker 仍可能是单点，未建立生产高可用 |
| 生产部署 | UNAVAILABLE | 当前仅等级 C，开发 Compose 不能直接生产使用 |

## 逐项证据

### 1. 认证 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/auth.py`、`apps/api/app/api/actor.py`、`apps/api/app/models.py`、前端 login/AuthGate。
- 模型/迁移：User、UserSession；`0001_initial`、`0008_teacher_sessions`。
- 自动化证据：`tests/test_auth.py` 覆盖登录、me、CSRF、退出、过期、production 禁 demo、教师隔离和初始化教师。
- 真实证据：HTTP72 两个教师真实登录；BROWSER72 登录与换教师；DOC-EVIDENCE 记录 Nginx 下登录/CSRF/Cookie 检查。
- 真实限速证据：production 登录限速使用 Redis 共享固定窗口状态，已验证双 API 实例累计失败次数；默认窗口 300 秒、阈值 5 次，Redis 不可用时 fail closed；限速 key 使用 HMAC，不包含明文密码。
- 限制：上述限速仅在本地预生产式双 API 环境验证；不代表公网 DDoS、WAF 或生产攻击面能力。Cookie 重放、多会话 UI、完整固定会话测试未完成；当前在线状态未复核。
- 可以说：数据库会话、scrypt、HttpOnly/SameSite、写请求 CSRF、production Secure/禁 demo 及本地双 API Redis 共享限速已实现，并在记录的开发验证范围内通过。
- 禁止说：企业级 IAM、完整 SSO/MFA、公网防爆破、WAF、DDoS 防护或生产认证已验收。
- 后续验收条件：完整会话攻击用例、Cookie 重放、多会话管理和生产 TLS/secret/攻击面验证。

### 2. 班级与学生 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/domain.py`，前端 classes 页面，CSV/XLSX 两阶段导入。
- 模型/迁移：Class、Student、ClassStudent、Group、ImportJob/Row/Error；`0002_classes_students_imports`。
- 自动化证据：`tests/test_classes.py` 覆盖 CRUD、归档、成员、分组、CSV/XLSX preview/confirm/idempotency；前端 classes tests。
- 真实证据：BUSINESS-E2E 覆盖创建唯一合成班级、CSV 预览/确认、3 人列表和前导零学号；PERF 覆盖 50 人列表。
- 第五部分证据：Class/Student/Group/Import 的适用操作已纳入 `AUTH-MATRIX`；真实学生数据仍禁止使用。
- 可以说：教师端班级/学生管理和合成数据导入流程已实现。
- 禁止说：已通过真实学校数据验证、可做正式学籍管理。
- 后续验收条件：第五部分矩阵已完成；后续仅持续回归异常/冲突导入、归档和分组边界。

### 3. 作业和 Rubric — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/assignments.py`、AssignmentWizard、作业/编辑/Rubric 页面。
- 模型/迁移：Assignment、PaperVersion、Question/Region、RubricVersion/Item、KnowledgePoint；`0003`、`0005`。
- 自动化证据：`tests/test_assignments.py` 覆盖 CRUD、文件/页面/题目/区域、Rubric、发布检查、拒绝上传。
- 真实证据：BUSINESS-E2E 覆盖创建、合成 PNG、页面、OCR 候选人工修正、一主观一客观题、正分值、题区、知识点、Rubric、完整性检查与发布；PERF 另覆盖列表/详情读取。
- 限制：只验证正常主路径；未知分值和其他发布错误分支仍主要由自动化覆盖。
- 可以说：作业、题目、版本化 Rubric 与发布前完整性检查已实现。
- 禁止说：异常路径、真实试卷内容或所有文件格式已完整验收。
- 后续验收条件：发布错误路径、PDF/JPEG/DOCX 组合和跨教师动作矩阵。

### 4. 试卷 OCR — `PARTIAL`

- 实现位置：`apps/api/app/recognition/pipeline.py`、`apps/api/app/api/recognition.py`、RecognitionWorkspace、OCR Worker。
- 模型/迁移：RecognitionJob、PageProcessingResult、RecognitionBlock、Candidate/Correction；`0003`、`0004`。
- 自动化证据：`tests/test_recognition.py` 覆盖转换/预处理、fake/unavailable、RapidOCR 小型印刷体、Block 持久化。
- 真实证据：`ocr-orchestration-capacity.json` 记录 Fake OCR 150/200/250 页完整编排；
  `ocr-capacity-results.json` 记录 RapidOCR 3.9.2 清晰印刷体 100/150/250 页独立阶梯，
  250 页 756.412 秒、峰值 RSS 约 878 MiB。
- 限制：Fake 结果只证明 API/Redis/Celery/PostgreSQL/MinIO 编排，不是 RapidOCR 吞吐；
  RapidOCR 结果只适用于指定开发机和运行时生成的清晰印刷体，无 CER/WER；手写数学未经
  验证；公式 Provider unavailable；LaTeX 不可靠；DOCX 缺 LibreOffice 时 unavailable。
- 可以说：Fake OCR 编排完成至 250 页；指定开发机的 RapidOCR 清晰印刷体吞吐完成至
  250 页，并输出文本、坐标、置信度。250 页是本轮测试上限，不是系统绝对上限。
- 禁止说：可靠公式/手写识别、高准确率、适合真实答卷自动评分。
- 后续验收条件：代表性数据集和准确率口径；公式/手写需独立 Provider 与证据；生产拓扑
  仍需重新进行吞吐、资源和故障恢复验证。

### 5. 学生作业 OCR — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/recognition/submission.py`、`apps/api/app/api/grading.py`、Submission OCR Worker。
- 模型/迁移：SubmissionRecognitionJob/Block、SubmissionPage、StudentAnswer/Region；`0006`、`0009`。
- 自动化证据：`tests/test_submission_workflow.py` 覆盖 Worker 幂等、答案写入、页面结构与 finalize guard。
- 真实证据：BUSINESS-E2E 经浏览器上传 4 张运行时合成图片，自动匹配两份 Submission，启动/等待 Celery Submission OCR，显示 StudentAnswer 并保存页面顺序。
- 限制：使用隔离 `APP_ENV=test` 的 Fake OCR 工作流适配器；只证明异步编排/持久化，不证明 RapidOCR、手写或公式能力。拆分/合并、匹配异常、低置信和重试未覆盖。
- 可以说：学生作业 OCR 的浏览器 UI 与异步工程链路在隔离合成环境闭环通过。
- 禁止说：RapidOCR 学生答卷准确率通过，或真实答卷 OCR 已验收。
- 后续验收条件：纯合成 PDF/图片经上传、匹配、异步 OCR、修正、重试到答案持久化的可审计验证。

### 6. 客观题评分 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/grading/providers.py`、`apps/api/app/api/grading.py`。
- 模型/迁移：GradingJob/Result、StudentAnswer、Rubric；`0006`。
- 自动化证据：`tests/test_grading.py` 验证大小写/空格规范化精确匹配；submission workflow 覆盖后续复核。
- 真实证据：BUSINESS-E2E 显示 `objective-rule/v1`、Criterion、Evidence 与 5 分建议，教师在复核 UI 接受后进入最终快照。
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

### 8. 教师复核 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/grading.py`、review 页面。
- 模型/迁移：TeacherReview、ScoreRevision、GradingEvidence/CriterionResult；`0006`。
- 自动化证据：复核决策、手动分数、批量资格、一致性、修改留痕、finalize guard。
- 真实证据：BUSINESS-E2E 在三栏工作台处理两份提交×两题；客观题接受，主观题 Provider unavailable 后分别人工输入 4/3 分，进度 4/4 后 finalize。
- 限制：低置信、异常、主观题、`score=null`、修正答案和 Rubric 变化必须复核；不能无条件批量接受。
- 可以说：教师可接受、修改、拒绝、手动评分并保留修订记录。
- 禁止说：AI 建议会自动成为最终成绩。
- 后续验收条件：浏览器逐题/批量资格、并发修改、审计与失败恢复验证。

### 9. 最终成绩 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/results/services.py` 的 FinalScoreService、`apps/api/app/api/grading.py` 的 `POST /submissions/{id}/finalize`。
- 模型/迁移：SubmissionScoreSnapshot、Submission.finalized；`0006`、`0007` details schema。
- 自动化证据：snapshot schema、分值范围、重复题目、合计、finalize guard 和模型分层测试。
- 真实证据：BUSINESS-E2E 从上传、规则初批、人工复核到 finalize 生成两份最新 complete Snapshot（9、8），并与 Release/报告/Analytics 对账。
- 限制：仅 finalized + 最新 complete；旧 schema 或不完整 details 拒绝；缺失不是零分。
- 可以说：正式成绩来源规则已实现并由自动化保护。
- 禁止说：GradingResult/建议分/临时复核是正式成绩，或未完成等于零分。
- 后续验收条件：合成数据真实 HTTP 从复核到多版本 Snapshot、旧/不完整 Snapshot 拒绝及审计验证。

### 10. GradeRelease — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/results.py`、`apps/api/app/results/services.py`。
- 模型/迁移：GradeRelease、GradeReleaseItem.score_snapshot_id；`0007`。
- 自动化证据：Release 从 FinalScoreService 建项、固定 Snapshot、报告/Analytics 服务读取固定 Release。
- 真实证据：BUSINESS-E2E 的 readiness 显示 2 可发布、1 未完成，由 UI 创建 Release 并固定两份具体 complete Snapshot；HTTP72 另验证既有固定 Release 读取。无学生端或通知。
- 限制：`released` 仅为教师确认数据；后续改分需新 Snapshot 与新 Release 版本。
- 可以说：发布版本固定具体成绩快照，可追溯且旧版不变。
- 禁止说：学生已收到、学生端已上线或已通知送达。
- 后续验收条件：真实创建/取消/多版本/改分后新发布验证；学生送达保持 OUT_OF_SCOPE。

### 11. XLSX/PDF — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/results/services.py`、`apps/api/app/results/jobs.py`、Report Worker；仓库内 Noto Sans SC。
- 模型/迁移：ReportJob、StoredFile、ReportJobStudentScope；`0007`、`0010`。
- 自动化证据：XLSX sheet/文本学号/公式防护，中文 PDF 字体与解析，Worker 只收 Job ID 且幂等。
- 真实证据：除 BUSINESS-E2E 和 retry 证据外，`async-capacity-results.json` 记录固定
  GradeRelease 的 50 名不同学生、50 个个人中文 PDF、1 个 50 行 XLSX 和包含 50 份
  不同学生 PDF 的 ZIP；52/52 真实 Celery/MinIO Job completed。
- 限制：只适用于记录的单 Worker 开发机与 50 人合成规模；更大规模、生产 SLA、对象恢复、
  故障条件和敏感导出控制未建立。
- 可以说：固定 GradeRelease 的 50 名不同学生报告、XLSX 和 ZIP 在开发环境真实完成。
- 禁止说：生产报告容量、生产下载 SLA 或任意更大规模已经验收。
- 后续验收条件：按生产拓扑扩展容量，并完成对象恢复、故障条件和敏感导出控制验证。

### 12. Analytics — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/results/services.py`、`apps/api/app/api/results.py`、Analytics 页面与学生详情。
- 模型/迁移：AnalyticsSnapshot 固定 GradeRelease；`0007`。
- 自动化证据：metrics 公式、分布、错误、知识点、主观题不宣称正确率、分页稳定性和前端状态。
- 真实证据：除 HTTP72/BROWSER72/BUSINESS-E2E 外，`analytics-capacity-results.json`
  覆盖 50/100/200 名学生与 20/50/100 题；同 Release 顺序重复和 20 路并发创建均复用
  同一 Snapshot，不同 Release 创建独立 Snapshot。
- 限制：最大规模学生趋势/详情约 7.7–7.9 秒，存在明显扩展边界；只证明单 API 开发机的
  功能与一致性，不证明生产容量、SLA、教学效果或多实例性能。
- 可以说：固定发布版本的统计、下钻、趋势和 Analytics 创建幂等在 200 人/100 题合成
  开发规模通过。
- 禁止说：AI 学情诊断、真实教学效果、生产规模分析。
- 后续验收条件：保持计算口径回归，并在生产拓扑下验证查询计划、更多实例、SLA 和故障条件。

### 13. TeachingInsight — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/api/results.py`、Analytics 页面。
- 模型/迁移：TeachingInsight.provider=`rule_based`，固定 AnalyticsSnapshot；`0007`。
- 自动化证据：生成内容只引用 metrics，确认时重校 evidence，状态与编辑规则有代码/测试支撑。
- 真实证据：HTTP72 验证生成、编辑、确认、重新生成、失效与跨教师隐藏；BROWSER72 验证编辑确认。
- 限制：固定规则和小样本，不是大模型或自主诊断；教师必须审阅。
- 可以说：规则型教学建议可编辑、确认、重新生成和失效。
- 禁止说：真实 AI 深度分析、大模型自主教学诊断。
- 后续验收条件：规则版本、解释性、边界样本和教师确认审计持续回归。

### 14. 文件安全 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：`apps/api/app/security/files.py`、各上传入口、通用文件路由。
- 模型/迁移：StoredFile.owner/status；`0001`。
- 自动化证据：`FILE-MATRIX` 记录 41/41 个运行时小型结构 fixture，覆盖 PDF、PNG/JPEG、DOCX/XLSX、公式注入、首/中/末非法批次全有或全无、同批重复校验值、存储写失败补偿和数据库提交失败补偿；本轮安全 marker 的孤儿增量为 0。
- 真实证据：test-only 有效期 2 秒的真实 MinIO 签名 URL 到期后返回 403；重新鉴权签发成功，旧 URL 不恢复有效。隔离 HTTP 同时验证 StoredFile metadata、signed URL 和 delete 的 owner 边界。
- 限制：fixture 是运行时生成的小型结构输入，不是外部攻击者渗透、真实恶意软件执行、无限文件类型覆盖或解析器模糊测试。第七部分只完成开发环境恢复；生产灾备、高可用、生产容量与运维仍未建立。
- 可以说：第五部分所列结构化文件安全 fixture 已通过；上传前内容校验、批次原子性、对象补偿、StoredFile owner 隔离和短期签名 URL 到期已验证。
- 禁止说：已抵御所有恶意文件、文件上传绝对安全、已通过外部渗透或生产安全认证、MinIO 灾备恢复已完成。
- 后续验收条件：生产前仍需独立完成外部安全评估、容量与资源约束验证，以及正式拓扑下的灾备、高可用和多实例恢复；不得把这些后续项反写为第五部分未完成。

### 15. 权限隔离 — `IMPLEMENTED_AND_VERIFIED`

- 实现位置：CurrentActor、各 owned 查询、StoredFile owner 校验。
- 模型/迁移：主要资源 owner_id/关联所有权散布于 0001–0010。
- 自动化证据：`AUTH-MATRIX` 包含 27 类资源、29 类操作、783 个资源×操作格，其中 117 个适用、666 个明确 `not_applicable`；六种身份共 702 个结果全部通过，并枚举全部业务路由的 Session/CSRF 边界。
- 真实证据：隔离栈 HTTP 16/16，覆盖未认证拒绝、缺失/错误 CSRF、Teacher B 跨教师隐藏和 StoredFile owner 隔离；既有 HTTP72 与 BROWSER72 继续提供 Analytics Teacher B HTTP/Edge 证据。
- 限制：结论只适用于第五部分定义的矩阵和间接引用范围，不是外部渗透测试，也不证明不存在任何越权漏洞。第八部分已验证 Redis 共享登录限速；Cookie 重放专项、完整多会话管理、生产部署和生产级故障恢复仍未完成。
- 可以说：第五部分定义的资源×操作权限矩阵已通过；跨教师 owner 隔离、Session/CSRF、矩阵覆盖的间接引用和文件访问边界已验证。
- 禁止说：已通过外部渗透、已证明不存在任何越权漏洞、已完成生产级 IAM 认证、多实例限速或 Cookie 重放专项。
- 后续验收条件：生产前仍需外部安全评估、多实例限速、Cookie/多会话专项以及生产部署与故障恢复；不得把这些后续项描述成第五部分矩阵未执行。

### 16. 性能 — `PARTIAL`

- 实现位置：`scripts/performance_smoke.py`、`PERFORMANCE.md`。
- 模型/迁移：不适用；fixture 使用合成 2×50 人、2×20 题。
- 自动化证据：`scripts/sync_concurrency_test.py`、`scripts/fake_ocr_capacity_test.py`、
  `scripts/ocr_capacity_test.py`、`scripts/report_capacity_test.py`、
  `scripts/analytics_capacity_test.py`；同步容量保留原始和中间 failed 证据，最终
  `sync-capacity-optimized.json` 为 passed；完整机器 JSON 定位见
  `PERFORMANCE-CAPACITY.md`。
- 真实证据：PERF 中五类同步接口 100% 成功，单客户端 P95 40.51–88.18 ms。
- 限制：结论仅适用于记录的单 API/单 Worker 开发机；最大规模 Analytics 学生读取约
  8 秒，未证明多实例、生产数据分布、SLA 或故障恢复。
- 可以说：第六部分开发机有界容量通过；详情并发门槛、250 页 Fake/真实 OCR、50 名
  不同学生报告及 200 人/100 题 Analytics 已有机器证据。
- 禁止说：已证明生产容量、OCR 准确率、手写/公式能力或生产 SLA。
- 后续验收条件：生产前按正式部署拓扑、真实脱敏分布、监控和 SLA 重新压测。

### 17. 备份与故障恢复 — `IMPLEMENTED_AND_VERIFIED（开发范围）`

- 实现位置：`BACKUP-RESTORE.md`、`FAILURE-RECOVERY.md`、`OPERATIONS.md`、恢复 Compose、
  只读 reconciliation 和两个正式摘要。
- 模型/迁移：全库、StoredFile 动态对象引用、RecognitionJob、ReportJob、
  AnalyticsSnapshot；Alembic `0010_report_student`。
- 自动化证据：恢复安全测试、Windows UTF-8/结构化结果测试、OCR/Report/Analytics
  定向测试，以及完整后端 113 passed、2 skipped；Ruff 113 files、mypy 52 files。
- 真实证据：RECOVERY7。`gate-20260724-static-a1` 完成 PostgreSQL custom-format 独立
  恢复和 MinIO 7/7 对象恢复；`fault-20260724-c84f19` 完成 12 个单 Worker 故障场景。
- 限制：只适用于纯合成、单 API/单 Worker 开发环境。观察 RPO 为 0 秒仅因备份窗口无写入；
  2.314 秒仅是独立数据库恢复；visibility 配置 15 秒而实际重投完成 102.230 秒。
  异地、加密、密钥轮换、长期、增量、生产规模、多实例和自动切换均未验证。
- 可以说：PostgreSQL 独立备份恢复、MinIO 独立对象恢复和单 Worker 故障恢复在记录的开发
  环境 PASS；StoredFile、文件解析、签名 URL、队列、幂等和孤儿对账通过。
- 禁止说：生产灾备、生产高可用、生产 RPO/RTO 或 SLA 已建立；不得把 Fake OCR、
  test-only 故障注入或 Docker Desktop 重启描述为生产能力。
- 后续验收条件：按正式部署拓扑验证加密/异地/增量/长期备份、多实例切换、监控告警、
  生产规模和经批准的 RPO/RTO。

### 18. 生产部署 — `UNAVAILABLE`

- 实现位置：开发 Compose、可选 Nginx 配置、Dockerfile、OPERATIONS。
- 模型/迁移：PostgreSQL 目标与 0010 head；不构成生产部署证据。
- 自动化证据：Compose/build/配置在既有文档中有检查记录。
- 真实证据：DOC-EVIDENCE 记录本地六服务和 Nginx 冒烟，仅为开发环境。
- 限制：第八部分本地预生产栈仅由 Nginx 回环发布 HTTPS；仍无正式证书、secret store、监控告警、完整容量/安全/灾备和发布审计。
- 可以说：提供开发部署骨架和本地代理配置。
- 禁止说：生产可用、可公网开放、可真实教学试点。
- 后续验收条件：项目已建立连续、可追溯的八提交链；生产能力仍需独立完整验收，当前保持 unavailable。

## 明确 OUT_OF_SCOPE

学生端、成绩通知/送达、公共注册、全自动主观题批改和大模型自主教学诊断不属于当前版本。GradeRelease 的存在不得改变这些范围判断。
> 第八部分：production 守卫、双实例认证/CSRF/Redis 共享限速、Nginx 暴露、单 API
> 切换、日志扫描和 Edge 均为 PASS；正式 Run 为 `v8-final-20260725-c6568104`，历史 Run
> `v8-20260725-000100` 保持 PARTIAL，以 preproduction 两份 JSON 为准。
> 原定八部分已经完成；任何后续工作属于重新规划的可选扩展，不自动形成新的编号部分。
