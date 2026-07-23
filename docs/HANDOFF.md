# AhaMark 教师核心业务浏览器闭环交接（2026-07-23）

第一部分的可审计基线入口为 `PROJECT-BASELINE.md`；能力状态以 `CAPABILITY-EVIDENCE-MATRIX.md` 为准。下文“通过”只描述既有检查记录，不自动表示生产可用。

## 最终结论

验收等级仍为 **C（内部演示或开发测试）**。第二部分“教师核心业务浏览器闭环”已关闭：独立栈中以纯合成数据通过 A–H 8/8。整个项目不因此升级到 B；完整资源隔离矩阵、异步/并发性能、完整恶意文件 fixture 和 MinIO 恢复仍未完成。

## 真实状态

- 服务：核心六服务均 healthy；可选 Nginx proxy 配置与语法通过。栈保持运行，三个命名卷保留。
- 数据库：活动库 `0010_report_student (head)`；独立空库 upgrade、`0010→0009→0010` 通过。
- 认证：scrypt、数据库 Session、HttpOnly/SameSite、production Secure、CSRF、撤销/过期、禁用用户、production 禁 demo 已实现并测试。
- OCR：第二部分使用 test-only Fake OCR 工作流适配器，只证明 UI/编排；不证明 RapidOCR。RapidOCR 3.9.2 印刷体窄范围 available，公式 OCR unavailable。
- Grading/Review：客观题规则评分、三栏复核、人工评分、批量资格和一致性已实现；主观题真实 AI Provider unavailable，必须人工评分。
- Snapshot/Release：唯一成绩来源为 finalized Submission 的最新 complete Snapshot；GradeRelease 固定 Snapshot，incomplete 不发布。
- XLSX/PDF：既有真实 Celery/MinIO 冒烟与自动化通过；本轮没有重新完成 30–50 份 PDF 容量测试。
- Analytics：除既有 35 请求和 6 步冒烟外，A–H 闭环已从固定 GradeRelease 验证参与人数、平均分、分布、学生详情、知识点、趋势和规则 Insight。

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

- 后端：第四部分完整门禁 53 passed，1 条第三方 Starlette TestClient 弃用警告；Ruff 与 mypy 通过。
- 前端：第四部分关闭轮 26 tests passed；Prettier、ESLint、TypeScript 通过。
- Next build：18 条路由构建通过；仍有 lockfile SWC 自动修补失败警告（未阻断构建）。
- 性能：五类同步接口 100%；P95 为 40.51–88.18 ms。仅单客户端开发冒烟。
- 文件安全：代码加固与小型自动化通过；完整 fixture 矩阵 PARTIAL。
- 隔离：Analytics 14 项真实跨教师拒绝 + 文件 3 类自动化拒绝；完整矩阵 PARTIAL。
- Worker：pause 时 `/ready` degraded/0 worker；unpause 后 pong 且 healthy。
- 代理：登录 200、缺 CSRF 403、正确退出 204、CSP/nosniff 可见。
- PostgreSQL 恢复：独立库恢复并验证 users/classes/assignments/submissions/snapshots/releases = 3/4/5/8/8/3。
- MinIO 恢复：NOT RUN；只读孤儿扫描发现合成 PDF 缺对象 1 条、旧 smoke 对象缺记录 1 条，未删除。

## 启动、停止与恢复

启动：`docker compose up --build -d`。代理：`docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d proxy`。停止且保留数据：`docker compose stop`。严禁 `down -v`。失败任务通过 API retry 创建新任务；数据库/对象恢复流程见 `docs/OPERATIONS.md`。

## 生产阻塞项

1. Class 至 TeachingInsight 的完整跨教师操作矩阵未跑完。
2. OCR、报告、Analytics 并发/吞吐、CPU、内存、队列等待与慢 SQL 未测。
3. 恶意文件所有 fixture 与真实签名 URL 到期测试未跑完。
4. MinIO 备份恢复、Redis/MinIO 中断恢复未验证。
5. 开发 Compose 暴露 MinIO 端口，未提供正式 TLS/secret/监控平台配置。
6. 第二部分修改尚未提交；当前可审计比较基线为 `f7783f0073592140c1400d6e7f41ffb17638c64e`。

后续维护先从 `docs/FINAL-ACCEPTANCE.md` 的 NOT RUN/PARTIAL 项开始，不要扩展学生端或宣称主观题 AI 自动评分。本任务没有提交、推送或部署。
