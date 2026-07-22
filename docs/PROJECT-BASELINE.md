# AhaMark 项目基线与能力边界

基线日期：2026-07-22

项目版本：`0.1.0`（`pyproject.toml`、`apps/web/package.json`）

当前分支：`master`

正式验收等级：**C（内部演示或开发测试）**

本文件是第一部分的基线入口。能力证据见 [CAPABILITY-EVIDENCE-MATRIX.md](CAPABILITY-EVIDENCE-MATRIX.md)，数据边界见 [DATA-SECURITY-BOUNDARIES.md](DATA-SECURITY-BOUNDARIES.md)，统一对外表述见 [PRODUCT-CAPABILITY-STATEMENTS.md](PRODUCT-CAPABILITY-STATEMENTS.md)。发生冲突时依次以当前代码和模型、迁移、自动化测试、真实 HTTP/浏览器记录、验收与运维文档、README 为准。

## 1. Git 与版本状态

- 仓库尚无 `HEAD`，没有任何提交；全部候选项目文件均未跟踪。因此当前版本号不能对应到可复核的提交，历史变更也不能通过 Git 追溯。
- 本任务开始及结束均未执行 `git add`、`commit`、`push`、建分支、历史重写或文件清理。
- 建议纳入首个基线：源码、全部 0001–0010 迁移、测试、交付文档、依赖清单与锁文件、Compose/Nginx 配置、脚本、字体及其许可证/来源说明。
- 必须排除：本地 `.env`、SQLite 数据库、虚拟环境、依赖目录、构建/缓存、日志、覆盖率结果、测试报告、浏览器认证状态、数据库备份、MinIO 数据/导出和生成的报告文件。
- 工作区已发现但已忽略：`.env`、`ahamark.db`、`ahamark-test.db`、`node_modules/`、`apps/web/.next/`、Python/测试/类型检查缓存和 `tsconfig.tsbuildinfo`。未删除任何一项。

本次新增忽略规则均为窄范围保护：`.env.*`（保留 `.env.example`）保护环境凭据变体；`.coverage*`/`htmlcov` 保护覆盖率生成物；根目录 Playwright/测试报告与认证状态规则保护截图、trace、报告及 Cookie 状态；根目录 backups/MinIO/reports/exports 规则保护备份、对象导出和生成报告；`*.dump`、`*.sqlite`、`*.sqlite3` 保护常见本地数据库与备份格式。目录规则锚定仓库根，避免误排除同名源码包、迁移、测试或文档。

## 2. 技术基线

- Web：Next.js 15.5.9、React 19.1.1、TypeScript 5.9 系列；根 npm workspace 指向 `apps/web`，`package-lock.json` 为 lockfile v3。
- API：Python >=3.11、FastAPI、SQLAlchemy 2、Alembic、Pydantic Settings。
- 异步：Celery + Redis；任务只接收 Job ID，并设置 late ack、worker-lost 重投、超时和 prefetch=1。
- 持久化：正式目标为 PostgreSQL 16；`alembic.ini` 的本地默认仍为 SQLite。对象存储为 MinIO。
- OCR：RapidOCR 3.x + ONNX Runtime，本地印刷体文字识别；公式 Provider 独立 unavailable。
- 报告：openpyxl 生成 XLSX，ReportLab 与仓库内 Noto Sans SC 字体生成中文 PDF。
- Compose 六个核心服务：`web`、`api`、`worker`、`postgres`、`redis`、`minio`；可选 `proxy` 使用 Nginx。

## 3. 数据模型与迁移

静态迁移链单头、连续：

```text
0001_initial
  -> 0002_classes_students_imports
  -> 0003_assignments_papers_rubrics
  -> 0004_recognition_pipeline
  -> 0005_nullable_question_score
  -> 0006_submissions_grading_review
  -> 0007_grade_release_reports_analytics
  -> 0008_teacher_sessions
  -> 0009_submission_recognition_blocks
  -> 0010_report_student (head)
```

关键模型包括 User/UserSession、Class/Student、Assignment/PaperVersion/RubricVersion、RecognitionJob/Block、Submission/StudentAnswer、GradingResult/TeacherReview/ScoreRevision、SubmissionScoreSnapshot、GradeRelease/Item、ReportJob、AnalyticsSnapshot 和 TeachingInsight。代码模型与 0010 head 的功能范围一致；本任务没有连接数据库、读取业务数据或执行迁移。

## 4. 最终成绩不变量

唯一正式成绩读取入口为 `FinalScoreService`，必须同时满足：

1. Submission 属于当前教师且 `status=finalized`；
2. 只取该 Submission 版本最高的 `SubmissionScoreSnapshot.status=complete`；
3. details schema、题目唯一性、分值范围、分题合计与顶层合计通过校验；
4. GradeReleaseItem 固定具体 `score_snapshot_id`；
5. Analytics、XLSX 和 PDF 从固定 GradeRelease 读取。

GradingResult、AI/规则建议分、临时 TeacherReview、incomplete 或 superseded Snapshot 均不是正式成绩。没有 complete Snapshot 表示“未完成”，不得记为零分。`released` 只表示教师确认一组固定成绩数据，不表示学生已收到、学生端已上线或通知已送达。

## 5. 证据基线

以下是既有证据记录，不代表本次重新执行：

- 自动化记录：后端 45 passed（1 条第三方警告）；前端 10 files / 20 tests；格式、lint、类型检查和 Next build 通过。来源为 `HANDOFF.md` 与 `FINAL-ACCEPTANCE.md`。
- 真实 HTTP：`analytics72-http-verification.json` 记录 35 个请求，覆盖登录、Analytics 下钻/趋势/学生详情、报告重试、规则建议生命周期、错误输入及 14 项跨教师隐藏。
- 浏览器：`analytics72-browser-smoke.json` 记录 6 步无头 Edge 冒烟，仅覆盖 Analytics 范围。
- 性能：`performance-results.json` 是真实 PostgreSQL、单 API、单客户端顺序请求的开发延迟冒烟；不证明并发或异步容量。
- 环境叙述证据：既有文档记录六服务、Nginx、Celery/MinIO 冒烟和 PostgreSQL 备份恢复通过；MinIO 恢复未运行。
- 本次未重新运行测试或运行时验证。只读查询当前 Docker 状态因本机 Docker 配置/引擎权限不可用而未获得结果，因此“当前服务仍在运行”不作为本基线的实时事实。

## 6. 已知环境与阻塞

- 本地 `.env` 存在非占位凭据，已被 Git 忽略且未输出任何值；它比 `.env.example` 少 25 个后续新增配置键，存在配置漂移。是否依靠代码默认值必须由运行环境单独确认。
- `Settings` 中的 `IMPORT_EXPIRY_HOURS` 未列入 `.env.example`，当前依赖代码默认值 24 小时；这是配置示例与代码的一处可审计差异，本任务不修改配置文件。
- `package-lock.json` 存在依赖版本与许可证字段，但 470 个 package entry 均无 `integrity` 字段；既有 Next build 还记录 SWC lockfile 自动修补警告。锁文件可用于版本解析，但供应链完整性证据较弱。
- `.dockerignore` 已排除当前 `.env`、数据库、依赖、构建与缓存，但未覆盖 `.env.*`、备份/MinIO 导出/报告目录；当前工作区未发现这些额外文件。本任务权限只允许文档与必要 `.gitignore` 调整，因此未修改 `.dockerignore`，在任何构建前必须单独核对 build context。
- 迁移 `0006` 与 `0007` 通过导入当前 `Base.metadata` 并按表名生成历史表，而不是在迁移内冻结完整表定义；当前链可静态解析，但未来模型漂移可能改变从空库重放历史迁移的结果。按任务边界仅记录，不修改迁移。
- 完整业务浏览器闭环、全资源权限矩阵、异步/并发容量、完整恶意文件 fixture、签名 URL 真实到期、Redis/MinIO 故障恢复和 MinIO 备份恢复证据缺失。
- 只读孤儿报告记录 1 个数据库记录缺对象及 1 个对象缺数据库记录；两者都是合成/旧 smoke 路径，未自动删除。
- UI 中仍存在未就地附带限制的“AI 批改”“AI 批改中”标签。后端行为有人工复核约束，但这些短标签单独截图或对外引用可能夸大能力；本任务按禁止修改业务代码的边界仅记录风险。

## 7. 当前允许用途与基线不包含内容

允许：纯合成数据内部演示、本地开发、自动化测试、内部产品评审。

不允许：真实学生数据、真实教学试点、生产部署、公网开放、将主观题描述为全自动 AI 评分、将规则建议描述为大模型教学诊断。

本基线不证明 OCR 准确率、手写/公式能力、主观题 AI 能力、并发容量、生产安全、灾备完整性、学生端送达或任何真实教学效果；也不包含业务代码、模型、迁移、前端、Docker、数据库或 MinIO 的变更。

## 8. 已识别的文档差异

- README 的学生作业章节曾写“最新迁移为 0006”，而仓库 head 与其他文档为 0010；应理解为“该子系统对应迁移为 0006”，不能当作仓库 head。
- `FINAL-ACCEPTANCE.md` 的 `PASS` 表示其列出的验收检查通过，不等同于本基线的 `IMPLEMENTED_AND_VERIFIED`，更不等于生产可用；能力级状态以证据矩阵为准。
- README 曾记录学生 Submission OCR “本机尚未完成 Celery/MinIO 异步联调”，后续交接只证明自动化链路，不存在可定位到该链路的真实 HTTP/浏览器记录；因此矩阵采用 `IMPLEMENTED_AUTOMATED_ONLY`。
- 既有文档记录“栈保持运行”，本次无法实时读取 Docker 引擎；该句只作为 2026-07-22 的历史验证记录，不作为当前在线状态保证。
- `.env.example` 未暴露 `Settings.import_expiry_hours`；本地 `.env` 又落后于当前示例 25 个键。两者均不证明运行错误，但必须视为配置基线漂移。
