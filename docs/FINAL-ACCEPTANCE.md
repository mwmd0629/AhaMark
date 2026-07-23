# AhaMark 最终验收报告

版本 `0.1.0`；更新日期 2026-07-23；分支 `master`；比较基线为 `f7783f0073592140c1400d6e7f41ffb17638c64e`。

本报告中的 `PASS` 仅表示对应验收检查在记录范围内通过，不等于整项能力已完成真实环境验证或达到生产可用。统一能力状态、数据边界和产品措辞以 `PROJECT-BASELINE.md`、`CAPABILITY-EVIDENCE-MATRIX.md`、`DATA-SECURITY-BOUNDARIES.md` 和 `PRODUCT-CAPABILITY-STATEMENTS.md` 为准。

## 最终结论

**等级 C：内部演示或开发测试。** 教师核心业务 A–H、第五部分权限/文件矩阵已用
纯合成数据通过；异步容量、对象恢复和生产运维仍不完整，不适合真实教学试点或生产。

## 验收矩阵

| 项目 | 状态 | 证据/说明 |
|---|---|---|
| 六核心服务 | PASS | web/api/worker/PostgreSQL/Redis/MinIO healthy；栈保持运行 |
| 数据库迁移 | PASS | 活动库 0010；独立空库 upgrade；0010→0009→0010 |
| 后端测试 | PASS | 第五部分完整门禁 87 passed；Ruff、mypy 通过；1 条第三方 Starlette TestClient 弃用警告 |
| 前端测试 | PASS | 第四部分关闭轮完整 Vitest：12 files、26 tests passed |
| 前端格式/lint/type | PASS | Prettier、ESLint、TypeScript |
| Next production build | PASS | 18 条路由构建成功；SWC lockfile 修补警告仍存在但未阻断 |
| Analytics HTTP | PASS | 35 请求，含 14 项 Teacher B 隔离 |
| Analytics 浏览器 | PASS | 6 步无头 Edge 冒烟 |
| 教师核心业务浏览器 A–H | PASS | 独立栈、无头 Edge、纯合成数据；8/8 阶段通过，见 BUSINESS-E2E 与机器 JSON |
| 全链路 API 集成 | PASS | 第三部分 5 项异常/版本测试通过；failed/expired ReportJob retry 另有真实 Edge 证据 |
| 完整权限矩阵 | PASS | 27 资源×29 操作；117 适用、666 N/A；身份结果 702/702 |
| 成绩正确性 | PASS | 第四部分金标准 v1/v2 全链路对账；Edge 12/12 含两类错误下钻、班级/学生趋势；未完成学生不记零 |
| XLSX/中文 PDF | PASS | 本轮真实浏览器创建，Celery/MinIO Job 均 completed；未跑批量容量 |
| 30–50 人性能 | PARTIAL | 2×50、2×20 题的 5 类同步 API；异步与并发未测 |
| MIME/恶意文件 | PASS | 41 个运行时结构 fixture；批次与存储/DB 回滚通过 |
| Auth/Session/CSRF | PASS | 登录、错误、Cookie、CSRF、过期、撤销、production 边界 |
| MinIO/签名 URL | PASS（安全范围） | owner 隔离；2 秒 test-only 真实到期与重签；恢复属后续 |
| Worker 故障 | PARTIAL | pause/unpause 与 ready/pong 通过；Redis 中断/超时任务未跑 |
| 反向代理 | PASS | nginx -t、health、登录、Cookie、CSRF、安全头 |
| PostgreSQL 备份恢复 | PASS | 独立库恢复计数 3/4/5/8/8/3 |
| MinIO 备份恢复 | NOT RUN | 只读孤儿扫描工具已提供 |
| 可用性/可访问性逐页 | NOT RUN | Analytics 冒烟不能替代完整巡检 |
| 真实主观题 AI | NOT APPLICABLE | Provider unavailable；主观题人工评分；production 禁 fake |

## 第五部分权限与文件安全

5A、5B 正式关闭：27 类资源×29 类操作，117 个适用格、666 个明确 N/A，六种身份
702/702；隔离栈 HTTP 16/16；文件结构 fixture 41/41；本轮孤儿增量为 0。P0/P1 无
未修项。本结论不是外部渗透或生产安全认证，也不完成多实例限速、Cookie 重放专项、
Redis/MinIO 故障恢复、MinIO 备份恢复、容量或生产运维；项目等级保持 C。

## 第三部分异常与版本一致性

实现与机器证据见 `docs/BUSINESS-EXCEPTIONS-AND-VERSIONING.md`、
`docs/business-exceptions-verification.json` 和
`docs/business-report-retry-verification.json`。核心不变量是 immutable complete
Snapshot、Release 固定 snapshot、Report/Analytics 固定 release，以及同一学生只选最新
合法 complete snapshot；未完成成绩不记零。真实 Edge bootstrap A–H 8/8，且 failed/expired
ReportJob retry 生命周期已通过专用真实 Edge 脚本验证，第三部分该验收缺口已关闭。

## 第四部分成绩正确性

第四部分正式关闭。金标准 `score-correctness.synthetic.invalid/20260723T080000Z` 证明正式成绩
只来自 finalized Submission 的合法 complete Snapshot，Release/报告/Analytics 固定版本，
缺交和未完成不记零。真实 Edge 12/12 额外核对客观题错误 3 条、主观题人工评分错误 4 条、
班级最新有效发布趋势 71.5%（4 人）及改分学生最新趋势 45/50（90.0%）。旧 v1 证据保持不变。
详见 `SCORE-CORRECTNESS.md` 与两份 `score-correctness-*.json`。这不改变项目整体 C 等级。

## 性能摘要

单客户端成功率均 100%。P50/P95：登录 45.48/82.33 ms；班级列表 13.24/40.51 ms；50 人列表 48.73/69.82 ms；作业列表 65.07/88.18 ms；20 题详情 39.02/63.10 ms。没有采集 Worker 总时长、队列等待、查询数、慢 SQL、CPU、峰值内存和大文件大小。

## 修复与风险

- P0 已修：任意对象键可未认证读取元数据、签名或删除。
- P1 已修：学生作业缺少真实内容/页数/像素检查且解析前写对象。
- P1 已加固：Worker 崩溃重投和超时配置。
- P2 未修：Next.js 构建的 SWC lockfile 自动修补警告；构建仍成功。

外部依赖：RapidOCR 可用但不保证手写/公式；公式 OCR unavailable；真实主观题 AI unavailable。发布含义是教师确认版本，不是已发送学生端。

第二部分新增 BUSINESS-E2E 与机器证据，并更新 HANDOFF、FINAL-ACCEPTANCE 和能力矩阵。本任务没有 git add、提交、推送、PR 或部署；没有删除 Volume；独立 E2E 栈和证据数据保持运行/保留，等待用户决定。
