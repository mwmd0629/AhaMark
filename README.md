# AhaMark

> **当前仓库状态（2026-07-28）：** 本地 `master` 功能基线位于
> `8746e1819d0dc78333ee8670c8ce763dc103b528`，比 `origin/master` 超前 2 个提交且尚未 push；Alembic
> 唯一 head 为 `0024_nullable_publish_readiness_due_at`；迁移 0011–0024 已进入 `master`。六步
> Assignment Generation（编排、元数据/文件分析、题目提取、答案与 Rubric 草稿、集中复核发布、
> Provider 调用审计）已按受控、仅建议方式落地。Provider 默认 `unavailable`，外部请求默认
> `false`，`suggestion-only=true`；AI 不能自动发布作业，也不能写入最终成绩。
> **REAL-PROVIDER QUALITY PENDING**。本地开发阶段由 Codex 代为执行需要 API 的草稿生成，
> 结果仍需教师确认。合入 `master` 不代表已部署，
> 本次合并也没有自动执行任何数据库迁移。本项目仍不代表 Production Ready。

> 原定第一至第八部分均已正式关闭，并已形成连续、可追溯的八提交链。第八部分功能基线为
> `cc9146a5edf001817915c020f7aa26bc8053b989`；本地预生产门禁 8A–8E 及 Edge 已 PASS，正式 Run 为
> `v8-final-20260725-c6568104`，证据入口见
> [`docs/PREPRODUCTION-READINESS.md`](docs/PREPRODUCTION-READINESS.md)。该门禁只证明本地 API
> 层故障切换，不建立生产高可用或灾备，项目等级仍为 C。
>
> 批改闭环最终集成基线包含 `4c6266b` 与 `8746e18`：Structured Rubric 使用题目真实满分，
> `manual_only` 可绑定空 `validation_rule`，集中审查过滤 stale/superseded 并限制人工解决动作，
> 浏览器门禁有界；failed ReportJob 只能创建新任务重试，XLSX 所有外部文本列均防公式注入。

AhaMark 是面向教师的 AI 作业批改与学情分析平台。当前已实现数据库会话认证、Submission OCR 工程链路、教师评分复核、不可变成绩发布、异步 Excel/中文 PDF 报告和版本化学情统计。RapidOCR 是真实本地印刷体 OCR；当前没有真实主观题 AI Provider，主观题必须人工评分。第五部分权限与文件安全、第六部分开发机有界容量及第七部分开发环境备份/故障恢复均已完成定义范围内验收。整体等级仍为 **C（内部演示或开发测试）**，不适合真实学生数据、真实教学试点、生产部署或公网开放。

## 当前可用的教师流程

- 作业创建向导支持试卷拖拽/点击上传、文件状态展示、页面缩略图预览和当前页切换。
- 截止时间支持“无截止时间”或手动设置日期与时间。
- 已发布作业详情页提供“上传学生作业”入口；教师可创建批改批次并上传 PDF/PNG/JPG/JPEG。
- 学生作业文件会按文件名中的学号或班级内唯一姓名自动匹配，歧义匹配需教师确认。
- 集中审查将问题翻译为教师可理解的说明，已解决问题默认收起，未解决阻塞项优先展示。
- AI Provider 当前默认不可用；由 Codex 代跑草稿生成时，结果仍作为待教师确认的建议，不自动发布或写入最终成绩。

## 本地运行与数据位置

推荐在 D 盘工作区运行：

```powershell
cd D:\OpenAIData\Workspaces\AhaMark
Copy-Item .env.example .env
docker compose up --build -d
Invoke-WebRequest -UseBasicParsing http://localhost:8000/health
```

Web 地址为 <http://localhost:3000>，API 健康检查为 <http://localhost:8000/health>。
Docker 数据、项目工作区和桌面交付文件已迁移到 `D:\OpenAIData`；C 盘保留的路径只是兼容性目录联接。
不要提交 `.env`、数据库文件、`node_modules` 或 `.next`。

## 第七部分：开发环境恢复验收

第七部分 7A–7D 为 PASS：

- PostgreSQL 独立逻辑备份恢复：开发环境 PASS
- MinIO 独立对象恢复、metadata、引用、文件解析及孤儿对账：开发环境 PASS
- 单 API/单 Worker 的 Worker、Redis、MinIO 故障恢复：开发环境 PASS
- 运维文档、脱敏摘要和证据收口：PASS

正式证据入口：

- [备份恢复手册](docs/BACKUP-RESTORE.md)
- [故障恢复手册](docs/FAILURE-RECOVERY.md)
- [备份恢复摘要](docs/backup-restore-verification.json)
- [故障恢复摘要](docs/failure-recovery-verification.json)

本结论不建立生产灾备、生产高可用、生产 RPO/RTO、SLA 或多实例恢复能力。异地、加密、
增量和长期备份均未验证。观察 RPO 为 0 秒仅因备份窗口无源写入；2.314 秒仅是独立数据库
恢复耗时。Broker visibility timeout 为 15 秒，正式重投完成观察值为 102.230 秒。

第一部分基线入口：`docs/PROJECT-BASELINE.md`。能力证据、数据安全边界和统一产品措辞分别见 `docs/CAPABILITY-EVIDENCE-MATRIX.md`、`docs/DATA-SECURITY-BOUNDARIES.md`、`docs/PRODUCT-CAPABILITY-STATEMENTS.md`。这些文档严格区分实现、自动化验证、真实环境证据和生产可用性。

## 教师认证与安全边界

正式会话认证位于 `app/api/auth.py`：密码使用标准库 scrypt（独立随机盐），登录创建随机数据库会话，浏览器只保存 HttpOnly `ahamark_session` Cookie；另有 SameSite=Lax CSRF Cookie，带会话的写请求必须发送 `X-CSRF-Token`。会话默认 12 小时，支持撤销、过期、当前用户与退出。production 登录限速使用 Redis 共享固定窗口状态，已验证双 API 实例累计失败次数；默认窗口 300 秒、阈值 5 次，Redis 不可用时 fail closed。限速 key 使用 HMAC，不包含明文密码。生产环境 Cookie 自动 Secure，且 `APP_ENV=production` 时绝不回退到 demo actor。

当前未开放公共注册。管理员在受控环境中执行 `python -m app.cli.create_teacher --email teacher@example.com --display-name 教师姓名`，按不回显提示输入密码，然后访问 `/login`。前端使用 Cookie，不把长期令牌写入 localStorage；教师布局通过 `/auth/me` 保护。开发期 demo actor 只有 `DEMO_ACTOR_ENABLED=true` 且非 production 时可用。共享限速仅在本地预生产式双 API 环境验证，不代表公网 DDoS、WAF 或生产攻击面能力。

## 最终成绩、发布与分析

唯一最终成绩入口是 `FinalScoreService`：Submission 必须属于当前教师且为 `finalized`，每个 Submission 只取版本最高的 `SubmissionScoreSnapshot.status=complete`，并严格校验 details。绝不回退到 GradingResult、AI 建议、临时 TeacherReview、incomplete 或 superseded 数据。没有快照是“未完成”，不是零分。

新版 details schema 包含题目 ID/题号/题型、最终分/满分、TeacherReview ID、最终错误代码/评语、知识点 ID、评分方法和确认时间；校验题目不重复、分值范围、题目存在、顶层分数与分题和一致。旧快照缺少必填字段时不会进入发布或统计。

`GradeRelease` 以作业/班级递增 version 保存发布记录，`GradeReleaseItem` 固定具体 ScoreSnapshot ID。released 的产品含义仅是“教师已确认发布数据，尚未发送到学生端”，不是学生已收到。修改成绩后需生成新快照和新发布版本，旧版本不变。

Excel 是真实 `.xlsx`，包含“成绩总表、题目统计、知识点统计、导出说明”。学号强制文本，缺失成绩不写零，外部文本防公式注入。API 只创建 ReportJob 并派发 job ID；`workers/tasks/reports.py` 幂等生成、写对象存储、登记 StoredFile。该边界已有自动化测试及真实 Celery/MinIO 冒烟。个人与批量学生 PDF 使用仓库内 Noto Sans SC TTF，来源、许可证和校验值见 `apps/api/assets/fonts/SOURCE.md`。

AnalyticsSnapshot 固定 GradeRelease，旧快照不覆盖。后端统一计算参与人数、平均/最高/最低/中位数、归一化分数段、题目得分率/满分率/零分率、客观题正确率、知识点掌握率、教师确认错误频次和透明 A/B/C/D 临时分层。未完成学生不进入分母；一题多知识点时完整计入每个知识点并明确样本。主观题不显示“正确率”。RuleBased 教学建议只引用快照 metrics 的题目 ID、得分率和样本数；没有真实 AI 教学助手。

主要 API：

- `GET /api/assignments/{assignment}/classes/{class}/grade-readiness`
- `POST/GET /api/grade-releases`、`GET /api/grade-releases/{id}`、`POST .../cancel`
- `POST /api/grade-releases/{id}/reports`、`GET /api/report-jobs/{id}`、`GET .../download`
- `POST /api/grade-releases/{id}/analytics`
- `POST /api/analytics/{id}/insights`

当前仓库 Alembic 唯一 head 为 `0024_nullable_publish_readiness_due_at`；`0010_report_student`
是下述报告与学情功能对应的历史迁移节点。`/analytics` 已包含加载、空、错误、小样本、0–100%
图表、键盘可访问表格和数据版本选择。分数段、题目、知识点、最终错误类型均可分页下钻；班级、
学生及知识点历史趋势只读取每份作业最新有效发布版本，缺失作业不记零。学生详情路由为
`/analytics/students/{studentId}`，展示发布成绩、各题最终值、知识点、教师确认评语、
ScoreRevision 与真实 ReportJob 状态。

Analytics 7.1 新增 API：

- `GET /api/analytics/{snapshot}/score-bands/{band}/students`
- `GET /api/analytics/{snapshot}/questions/{question}/students`
- `GET /api/analytics/{snapshot}/knowledge-points/{knowledge_point}`
- `GET /api/analytics/{snapshot}/errors/{error_type}`
- `GET /api/classes/{class}/analytics/trends`
- `GET /api/students/{student}/analytics/trends`、`GET /api/students/{student}/analytics`
- `GET /api/classes/{class}/knowledge-points/{knowledge_point}/trend`
- `GET /api/students/{student}/knowledge-points/{knowledge_point}/trend`
- `GET /api/students/{student}/report-jobs`
- `GET/PATCH /api/teaching-insights/{insight}`、`POST .../confirm`、`POST .../regenerate`、`POST .../invalidate`

所有下钻使用 CurrentUser，并校验 AnalyticsSnapshot、Assignment、Class 与固定 GradeRelease 的所有权；列表默认 20 条、最多 100 条并稳定排序。教学建议明确标记为“规则型教学建议”，保留原始内容及编辑历史，确认后不可静默修改，evidence 数字在确认时与固定 AnalyticsSnapshot 再校验。前端未引入图表库，使用原生 HTML/CSS，因此无新增许可证和锁文件变化。

历史上的后续接手条件包含浏览器闭环、性能、安全专项、代理和隔离矩阵；第五部分权限与
文件安全、第六部分开发机容量和第七部分开发环境恢复现已完成定义范围内验收。生产容量、
生产灾备、高可用、正式部署和运维体系仍未建立。当前仍没有真实主观题 AI Provider，
主观题必须教师人工评分。

## 验收与交付入口

- 最终验收：`docs/FINAL-ACCEPTANCE.md`
- 安全与文件策略：`docs/SECURITY-AUDIT.md`、`docs/FILE-SECURITY.md`
- 性能结果：`docs/PERFORMANCE.md`、`docs/performance-results.json`
- 部署、代理、备份恢复和排障：`docs/OPERATIONS.md`
- 最终交接：`docs/HANDOFF.md`

本地生产样式代理（不含 TLS）使用：

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up --build -d
Invoke-WebRequest -UseBasicParsing http://localhost:8080/health
```

50 人合成数据可重复初始化与安全清理：

```powershell
docker compose exec -T api python -m app.cli.seed_performance_demo
python scripts/performance_smoke.py
docker compose exec -T api python -m app.cli.cleanup_performance_demo --confirm-marker performance50.synthetic.invalid
```

清理命令只接受固定 marker，并在事务中校验固定教师 ID/邮箱、打印范围；不会删除结构、Bucket、未知对象或 Docker Volume。

### Analytics 7.2 真实验证与 UI

非破坏性更新测试栈（保留 PostgreSQL、Redis、MinIO 命名卷）：

```powershell
docker compose up --build -d api worker web
docker compose exec -T api alembic upgrade head
docker compose exec -T api python -m app.cli.seed_analytics_demo
python scripts/verify_analytics_http.py docs/analytics72-http-verification.json
node scripts/analytics_browser_smoke.mjs
```

`seed_analytics_demo` 使用固定 UUID 和 `analytics72.synthetic.invalid` 标记，幂等创建两名合成教师、两个隔离班级、三名主场景学生、三份不同满分作业、三次 GradeRelease、一次缺交、两个 KnowledgePoint、两种最终错误类型、ScoreRevision、completed/failed ReportJob 和规则型 TeachingInsight。数据均为合成值；重复执行不会重复插入。验证完成后，只能用明确标记清理：

```powershell
docker compose exec -T api python -m app.cli.cleanup_analytics_demo --confirm-marker analytics72.synthetic.invalid
```

清理命令先验证固定教师 ID 与合成邮箱，只删除这两个 owner 的数据；不要用 `docker compose down -v`。真实 HTTP 脚本使用 Cookie+CSRF，验证四类分页下钻、稳定排序、三类趋势、学生详情、ScoreRevision、报告重新生成、TeachingInsight 生命周期、404/422 和 Teacher B 隔离，并将无密码、Cookie 或 CSRF 的结果写入 `docs/analytics72-http-verification.json`。

Analytics UI 现提供规则建议查看、evidence、编辑、草稿、确认、重新生成、失效、状态、loading/disabled 和成功/错误提示；明确标记为规则型建议。学生详情提供 0–100% 学生得分率折线图、按 KnowledgePoint ID 的掌握率折线图及等价表格。failed、expired、partially_completed 报告按钮调用 `POST /api/report-jobs/{id}/retry` 创建新 ReportJob；不是恢复原任务。completed 报告每次重新请求短期签名 URL。

Analytics 范围的无头 Edge 冒烟覆盖 Teacher A 登录、选择真实发布、分数段下钻、
Insight 编辑确认、学生和知识点趋势，以及 Teacher B 学生详情拒绝。完整业务浏览器
E2E、第五部分安全专项和第六部分开发机有界容量现已完成。第六部分在单 API/单 Worker
合成环境覆盖 50 名不同学生报告、Fake/RapidOCR 至 250 页及 200 人/100 题 Analytics；
最大规模 Analytics 学生读取约 8 秒。生产容量、SLA、多实例扩展、故障恢复和生产部署
验收仍属于后续范围。

## 学生作业与批改流程

教师为已发布且 PaperVersion/RubricVersion 完整的作业创建 GradingBatch，上传 PDF/PNG/JPG/JPEG。后端使用随机对象键保存文件，学号精确匹配优先，其次是班级内唯一姓名；重名、多个标识或无标识只生成待确认记录。一个学生的多张图片或 PDF 页面按文件顺序归并为同一 Submission，原文件和 SubmissionPage 均保留，不静默覆盖重复校验值。

Submission OCR 数据与试卷 RecognitionJob/PaperPage 隔离：学生域使用 SubmissionRecognitionJob、SubmissionPage、StudentAnswer 和 StudentAnswerRegion，坐标仍为未旋转原始页左上角 0–1。`recognized_*` 永久保留原始值，`corrected_*` 有值时评分优先读取修正值。空白、低置信、公式不可用和失败是不同状态。现有 RapidOCR 转换/预处理组件可复用于学生页；第六部分已完成 Fake OCR 的 Celery/MinIO 150/200/250 页编排和独立 RapidOCR 清晰印刷体 100/150/250 页吞吐阶梯，但二者不能互相替代，也不证明真实学生答卷准确率、手写或公式能力。

客观题 `single_choice`、`multiple_choice`、`true_false`、`fill_blank` 采用大小写与空格规范化后的确定性精确匹配，使用标准答案及可接受答案。单位/精度等无法由明确规则判断时应进入人工复核。主观题使用统一 GradingProvider；默认 UnavailableProvider 返回 `score=null`。FakeGradingProvider 只允许非 production 自动化测试，production 配置 fake 会安全降级为 unavailable，绝不能作为真实 AI 成绩。

教师复核支持接受、修改、拒绝、手动评分和需要更多信息。修正答案会使旧建议 superseded；每次最终分数/评语变化写 ScoreRevision。低置信、OCR/公式异常、`score=null`、Provider unavailable、修正答案和 Rubric 版本变化均不能直接成为最终成绩。当前 API 未开放“一键无条件接受”，批量接受必须在后续 UI 完善时复用同一后端资格规则。

`POST /api/submissions/{id}/finalize` 会逐题检查答案、教师最终分、分值范围、强制复核和当前 RubricVersion，并生成新的 SubmissionScoreSnapshot 版本而不覆盖旧版本。第七部分只能读取最新 `status=complete` 快照；`details` 保存每题 question/answer/review ID、最终分、满分、错误类型和评语。AI/规则 GradingResult 不是最终成绩来源。

学生作业与批改子系统对应迁移为 `0006_submissions_grading_review`；仓库当前 Alembic 唯一 head
为 `0023_assignment_provider_invocation_audit`：

```powershell
python -m alembic upgrade head
python -m alembic upgrade head --sql
python -m pytest -q
npm.cmd run format
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
```

## 技术栈与本地运行

- Web：Next.js 15、React 19、TypeScript、Tailwind CSS 4。
- API：FastAPI、SQLAlchemy 2、Alembic；生产数据库为 PostgreSQL。
- Worker：Celery + Redis；对象存储：MinIO。
- 文字 OCR：RapidOCR 3.9.2 + ONNX Runtime 1.27.0（本地处理，不上传第三方）。

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ocr]"
npm.cmd install
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir apps/api --reload
.\.venv\Scripts\python.exe -m celery -A workers.celery_app:celery_app worker --loglevel=INFO
npm.cmd run dev
```

复制 `.env.example` 为 `.env`、替换专用凭据后运行 `docker compose up --build -d`。Compose 定义 PostgreSQL 16、Redis 7、MinIO、API、Worker 和 Web；2026-07-22 已完成空库迁移、六服务启动、Celery 往返和 MinIO 上传/读取/签名 URL 冒烟。

## 分值完整性规则

迁移 `0005_nullable_question_score` 允许草稿/OCR 题目的 `Question.max_score` 为 `null`。未知分值不会写入 0、1 或其他哨兵值：候选可保留 `suggested_score=null`，确认后成为“分值未设置”的待完善题目。此类题目不能保存 Rubric、不能发布；发布检查返回题目 ID、题号和步骤：`QUESTION_SCORE_REQUIRED` 与 `ASSIGNMENT_TOTAL_SCORE_INCOMPLETE`。总分只汇总已知值，同时报告完整性错误。手工题目输入仍要求正数。

## OCR Provider 与边界

配置 `RECOGNITION_PROVIDER`：

- `rapidocr`：真实本地印刷体文字 OCR。安装 `pip install -e ".[ocr]"`；随 RapidOCR 包使用 `PP-OCRv6_det_small.onnx`、`ch_ppocr_mobile_v2.0_cls_mobile.onnx`、`PP-OCRv6_rec_small.onnx`。输出文字、0–1 页面坐标、0–1 置信度、provider/version/source/status；不生成 LaTeX。
- `fake`：只允许非生产自动化测试。`APP_ENV=production` 时选择 fake 会降级为 unavailable，不能用它评估准确率或宣称真实 OCR 可用。
- `unavailable`（默认）：明确禁用识别，但转换/预处理仍可用。

真实最小验证使用运行时合成、无个人信息的小图：清晰中文印刷体、中英数字、空白、低对比度和损坏字节；验证了文本、坐标、置信度、空结果、错误映射和 RecognitionBlock 持久化。样本极小，未计算 CER/WER，不代表真实教学、手写、公式、表格或几何能力。公式 provider 独立为 unavailable；普通数学字符只保留为 text 并进入人工复核。DOCX 仍因缺少 LibreOffice headless 返回 `DOCX_CONVERTER_UNAVAILABLE`。

## 文件与异步链路

API 创建 RecognitionJob 后只向 Celery 发送 job ID；派发失败会把数据库任务标为 `failed/WORKER_UNAVAILABLE`。Worker 从数据库和对象存储重新读取输入，写入 rendered/processed/thumbnail 键及页面、Block、Candidate。任务和页面重试复用数据库页面记录；状态以数据库为用户可见真相。

MinIO 原始键位于 `assignments/...`，衍生键位于 `recognition/{owner}/{job}/{page}/{kind}-{uuid}.png`，API 逐级校验 owner 后返回短期签名 URL。当前实现只按明确对象键操作，没有宽泛孤儿清理。2026-07-22 已在专用 Docker 测试栈完成 PostgreSQL 在线迁移与回滚再升级、Redis/Celery 消费、MinIO 上传/读取和签名 URL 生成；这是开发环境连通性证据，不是生产容量或安全证明。

## 健康与验证

`/health` 保持轻量；`/ready` 在短超时内分别报告 `postgresql`、`redis`、`celery_worker`、`minio`、`text_ocr`、`formula_ocr` 的 available/unavailable/degraded 状态，不返回凭据。FakeProvider 只会让文字 OCR 显示 degraded。

PostgreSQL 专用测试库示例（执行 downgrade 前必须再次确认目标不是生产库）：

```powershell
$env:DATABASE_URL='postgresql+psycopg://ahamark:<password>@localhost:5432/ahamark_55_migration_test'
python -m alembic upgrade head
python -m alembic current
python -m alembic downgrade 0004_recognition_pipeline
python -m alembic upgrade head
```

离线 DDL 与完整质量命令：

```powershell
$env:DATABASE_URL='postgresql+psycopg://ahamark:integration-only@127.0.0.1:5432/ahamark_55_migration_test'
python -m alembic upgrade head --sql
python -m alembic downgrade 0005_nullable_question_score:0004_recognition_pipeline --sql
python -m ruff format --check apps/api workers tests
python -m ruff check apps/api workers tests
python -m mypy
python -m pytest -q
npm.cmd run format
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
```

历史验证记录详见 `docs/HANDOFF.md`。第七部分关闭轮当时复用刚完成的后端门禁：
113 passed、2 skipped，Ruff format/check 113 files，mypy 52 files；7D 另执行 JSON、原始
证据哈希、Markdown UTF-8、相对链接、陈旧口径、敏感字段和 Git diff 门禁。该轮结束时
第七部分工作树仍未暂存、未提交、未推送或部署；随后第一至第八部分均已提交。
