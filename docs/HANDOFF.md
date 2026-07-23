# AhaMark 教师核心业务浏览器闭环交接（2026-07-23）

第一部分的可审计基线入口为 `PROJECT-BASELINE.md`；能力状态以 `CAPABILITY-EVIDENCE-MATRIX.md` 为准。下文“通过”只描述既有检查记录，不自动表示生产可用。

## 最终结论

验收等级仍为 **C（内部演示或开发测试）**。第二部分教师核心业务浏览器闭环、
第五部分权限与文件安全以及第六部分开发机有界容量均已关闭。第六部分通过不自动升级到
B；生产容量与 SLA、多实例扩展、Redis/MinIO 故障恢复、MinIO 备份恢复和生产运维仍未完成。

## 真实状态

- 服务：核心六服务均 healthy；可选 Nginx proxy 配置与语法通过。栈保持运行，三个命名卷保留。
- 数据库：活动库 `0010_report_student (head)`；独立空库 upgrade、`0010→0009→0010` 通过。
- 认证：scrypt、数据库 Session、HttpOnly/SameSite、production Secure、CSRF、撤销/过期、禁用用户、production 禁 demo 已实现并测试。
- OCR：第二部分 UI 闭环使用 test-only Fake OCR；第六部分另行完成 Fake 编排
  150/200/250 页和 RapidOCR 3.9.2 清晰印刷体 100/150/250 页吞吐阶梯。Fake 与真实
  吞吐证据不能互换，也不证明准确率、手写或公式能力；公式 OCR unavailable。
- Grading/Review：客观题规则评分、三栏复核、人工评分、批量资格和一致性已实现；主观题真实 AI Provider unavailable，必须人工评分。
- Snapshot/Release：唯一成绩来源为 finalized Submission 的最新 complete Snapshot；GradeRelease 固定 Snapshot，incomplete 不发布。
- XLSX/PDF：第六部分固定 Release 的 50 名不同学生已完成 50 PDF、50 行 XLSX 和
  包含 50 份不同学生 PDF 的 ZIP；52/52 Celery/MinIO Job completed。
- Analytics：除既有 35 请求、6 步冒烟和 A–H 闭环外，第六部分已覆盖 50/100/200 人、
  20/50/100 题以及同 Release 顺序/20 路并发幂等；最大规模学生读取约 8 秒。

## 第二部分浏览器证据

- 最终轮次：`business-e2e-20260722164927325.business-e2e.synthetic.invalid`，A–H 8/8 PASS。
- complete Snapshot：`32cfff83-75f5-40e0-a7f5-223ea549add7` = 9，`b8a01797-790a-49d8-b1f3-d70f2d6f2295` = 8。
- GradeRelease：`c68f7259-3f6a-44dc-8f59-df6833b1e67f`，固定上述两个 ID。
- 报告：XLSX 与中文个人 PDF Job 均 completed；Analytics 参与 2 人、平均 8.5；第 3 名未完成学生不记零。
- 人类报告：`docs/BUSINESS-E2E.md`；机器证据：`docs/business-e2e-verification.json`。

## 第三部分：异常业务与版本一致性

- 已实现 A–H 异常边界：OCR null/低置信度/公式不可用、歧义匹配幂等确认、导入全有或全无、拆分合并校验、Rubric stale/regrade、incomplete finalize、release/report/analytics/Insight 版本固定。
- 新增 `tests/test_exception_versioning.py`（5 项）；真实 Edge 异常 bootstrap 多轮 A–H 8/8；专用 retry 脚本已覆盖 failed/expired ReportJob 的新旧 Job、Release、学生范围、报告类型和刷新对账。
- 关键修复：就绪检查按学生选择最新合法 complete Snapshot；regrade 后清除答案 stale 标记；旧 Snapshot、Release、Analytics、Insight 内容保持不变。
- 详见 `docs/BUSINESS-EXCEPTIONS-AND-VERSIONING.md` 与 `docs/business-exceptions-verification.json`。

## 第四部分：成绩正确性专项

- 正式关闭：纯合成金标准 `score-correctness.synthetic.invalid/20260723T080000Z` 已完成
  Snapshot、GradeRelease、XLSX、中文 PDF、Analytics、学生详情和规则型 TeachingInsight 对账。
- v1 成绩 48/18/32/40，v2 仅改分学生变为 45；缺交和未 finalized 学生均未记零或进入分母。
- 旧 Snapshot、Release、报告、Analytics 和 Insight 保持不变；趋势只读取每份作业最新有效
  Release v2。
- 真实 Edge 12/12：含两类错误下钻（3/3、4/4）、班级趋势（1 点、4 人、71.5%）和改分学生
  趋势（1 点、45/50、90.0%）。
- 前端门禁：Prettier、ESLint、TypeScript、Vitest 26/26 全部通过；后端既有完整门禁
  53 passed，Ruff 与 mypy 通过。
- 证据：`docs/SCORE-CORRECTNESS.md`、`docs/score-correctness-verification.json` 和
  `docs/score-correctness-browser-verification.json`。

## 第八部分变更

- P0：修复通用文件元数据、删除、签名 URL 无认证/无 owner 校验。
- P1：统一上传内容检查；PDF/图片/Office 安全限制；学生作业整批先验后存；存储错误回滚。
- P1：Worker late ack、worker lost 重投、soft/hard time limit、prefetch=1。
- 增加 Nginx 本地代理、安全响应头、上传与超时限制。
- 增加 2×50 人幂等合成数据、精确 marker 清理和延迟脚本。
- 增加只读 MinIO/数据库孤儿扫描。
- 增加性能、安全、文件策略、运维和最终验收文档。

## 验证汇总

- 后端：第五部分完整门禁 87 passed，1 条第三方 Starlette TestClient 弃用警告；Ruff 与 mypy 通过。
- 前端：第四部分关闭轮 26 tests passed；Prettier、ESLint、TypeScript 通过。
- Next build：18 条路由构建通过；仍有 lockfile SWC 自动修补失败警告（未阻断构建）。
- 性能：五类同步接口 100%；P95 为 40.51–88.18 ms。仅单客户端开发冒烟。
- 文件安全：第五部分 41/41 个运行时结构 fixture 通过，批次原子性、对象补偿和真实
  MinIO 短期 URL 到期/重签已验证；不代表外部渗透或生产安全认证。
- 隔离：第五部分 27×29 矩阵、702/702 身份结果与隔离 HTTP 16/16 通过；多实例限速、
  Cookie 重放和完整多会话管理仍属后续。
- Worker：pause 时 `/ready` degraded/0 worker；unpause 后 pong 且 healthy。
- 代理：登录 200、缺 CSRF 403、正确退出 204、CSP/nosniff 可见。
- PostgreSQL 恢复：独立库恢复并验证 users/classes/assignments/submissions/snapshots/releases = 3/4/5/8/8/3。
- MinIO 恢复：NOT RUN；只读孤儿扫描发现合成 PDF 缺对象 1 条、旧 smoke 对象缺记录 1 条，未删除。

## 启动、停止与恢复

启动：`docker compose up --build -d`。代理：`docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d proxy`。停止且保留数据：`docker compose stop`。严禁 `down -v`。失败任务通过 API retry 创建新任务；数据库/对象恢复流程见 `docs/OPERATIONS.md`。

## 生产阻塞项

1. 第六部分开发机有界容量已通过，但最大规模 Analytics 学生趋势/详情约 7.7–7.9 秒；
   生产容量、SLA、多实例扩展、故障条件和生产数据分布尚未建立。
2. MinIO 备份恢复、Redis/MinIO 中断恢复未验证。
3. 开发 Compose 暴露 MinIO 端口，未提供正式 TLS/secret/监控平台配置。
4. 第五部分已提交为 `8ba3a413c4d7864506294ce728fa4f4dffeefce2`；第六部分工作树
   修改仍全部未暂存、未提交、未推送、未部署。

## 第五部分：权限与文件安全

- 5A：27 资源×29 操作，117 适用、666 N/A，六身份 702/702 PASS；真实 HTTP 16/16。
- 5B：41 个运行时结构 fixture 符合预期；PDF/图片/Office/公式注入、批次全有或全无、
  重复校验值、存储与数据库失败补偿通过。
- StoredFile：Teacher B metadata/签名/删除 404；test-only 2 秒 URL 到期 403，重签通过。
- 无未修 P0/P1；全局孤儿扫描含既有 recognition 派生对象口径噪声，未自动删除。
- 证据：`AUTHORIZATION-MATRIX.md`、`FILE-SECURITY-VERIFICATION.md` 及对应 JSON。

后续维护先从 `docs/FINAL-ACCEPTANCE.md` 中生产容量/SLA、故障恢复、备份恢复和生产运维
等 NOT RUN/PARTIAL 项开始；第六部分不再是未完成项。不要扩展学生端或宣称主观题 AI
自动评分。本轮第六部分工作树尚未提交、推送或部署。

## 第六部分容量交接（2026-07-23）

- 6A PASS：最终 `sync-capacity-optimized.json` 为 passed，同步矩阵 1600/1600；原始
  `sync-capacity-baseline.json` 与中间 `sync-capacity-results.json` 均为应保留的
  failed 历史问题证据。学生列表和详情 N+1 已修复，最终轮次 20/50/100 题详情并发 20
  P95 为 184.78/199.72/348.81 ms。
- 6B PASS：Fake OCR 150/200/250 页编排和真实 RapidOCR 100/150/250 页均完成。
- 固定 Release 的 50 名不同学生：50 PDF、50 行 XLSX、含 50 个 PDF 的 ZIP 全部核验。
- 50/100/200 人、20/50/100 题 Analytics 完成；顺序与 20 路并发重复创建均复用同一
  Snapshot。最大规模学生读取约 8 秒，属于开发机容量边界。
- 后端门禁 87 passed、2 skipped；Ruff 与 mypy 通过；无前端改动、无迁移。
- 证据入口：`PERFORMANCE-CAPACITY.md`、`sync-capacity-baseline.json`（原始 failed）、
  `sync-capacity-results.json`（中间 failed）、`sync-capacity-optimized.json`（最终 passed）、
  `ocr-orchestration-capacity.json`、`ocr-capacity-results.json`、
  `async-capacity-results.json`、`analytics-capacity-results.json`。
- 适用边界：仅证明指定开发机、单 API/单 Worker 和合成数据；250 页是本轮测试上限，
  不证明系统绝对上限、OCR 准确率、手写/公式能力、生产容量或生产 SLA。
