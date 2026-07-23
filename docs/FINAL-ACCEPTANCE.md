# AhaMark 最终验收报告

版本 `0.1.0`；更新日期 2026-07-23；分支 `master`；比较基线为 `f7783f0073592140c1400d6e7f41ffb17638c64e`。

本报告中的 `PASS` 仅表示对应验收检查在记录范围内通过，不等于整项能力已完成真实环境验证或达到生产可用。统一能力状态、数据边界和产品措辞以 `PROJECT-BASELINE.md`、`CAPABILITY-EVIDENCE-MATRIX.md`、`DATA-SECURITY-BOUNDARIES.md` 和 `PRODUCT-CAPABILITY-STATEMENTS.md` 为准。

## 最终结论

**等级 C：内部演示或开发测试。** 教师核心业务 A–H 已以纯合成数据通过真实浏览器闭环，但这不自动升级整个项目等级。完整权限矩阵、异步容量、恶意文件矩阵和对象恢复证据仍不完整，不适合受控真实教学试点或生产。

## 验收矩阵

| 项目 | 状态 | 证据/说明 |
|---|---|---|
| 六核心服务 | PASS | web/api/worker/PostgreSQL/Redis/MinIO healthy；栈保持运行 |
| 数据库迁移 | PASS | 活动库 0010；独立空库 upgrade；0010→0009→0010 |
| 后端测试 | PASS | 46 passed；Ruff、mypy 通过；1 条第三方 Starlette TestClient 弃用警告 |
| 前端测试 | PASS | 10 files / 20 tests |
| 前端格式/lint/type | PASS | Prettier、ESLint、TypeScript |
| Next production build | PASS | 18 条路由构建成功；SWC lockfile 修补警告仍存在但未阻断 |
| Analytics HTTP | PASS | 35 请求，含 14 项 Teacher B 隔离 |
| Analytics 浏览器 | PASS | 6 步无头 Edge 冒烟 |
| 教师核心业务浏览器 A–H | PASS | 独立栈、无头 Edge、纯合成数据；8/8 阶段通过，见 BUSINESS-E2E 与机器 JSON |
| 全链路 API 集成 | PARTIAL | 教师正常主路径已有真实浏览器链；异常矩阵与其他范围仍不完整 |
| 完整权限矩阵 | PARTIAL | Analytics 与文件隔离通过；全部资源/动作未覆盖 |
| 成绩正确性 | PASS | 浏览器 finalize 得到 9/8 两份 complete Snapshot；Release、报告、Analytics 对账一致，未完成学生不记零 |
| XLSX/中文 PDF | PASS | 本轮真实浏览器创建，Celery/MinIO Job 均 completed；未跑批量容量 |
| 30–50 人性能 | PARTIAL | 2×50、2×20 题的 5 类同步 API；异步与并发未测 |
| MIME/恶意文件 | PARTIAL | 统一内容检查和小型回归；完整 fixture 未跑 |
| Auth/Session/CSRF | PASS | 登录、错误、Cookie、CSRF、过期、撤销、production 边界 |
| MinIO/签名 URL | PARTIAL | owner 隔离自动化；孤儿扫描 1+1；真实到期和恢复未跑 |
| Worker 故障 | PARTIAL | pause/unpause 与 ready/pong 通过；Redis 中断/超时任务未跑 |
| 反向代理 | PASS | nginx -t、health、登录、Cookie、CSRF、安全头 |
| PostgreSQL 备份恢复 | PASS | 独立库恢复计数 3/4/5/8/8/3 |
| MinIO 备份恢复 | NOT RUN | 只读孤儿扫描工具已提供 |
| 可用性/可访问性逐页 | NOT RUN | Analytics 冒烟不能替代完整巡检 |
| 真实主观题 AI | NOT APPLICABLE | Provider unavailable；主观题人工评分；production 禁 fake |

## 性能摘要

单客户端成功率均 100%。P50/P95：登录 45.48/82.33 ms；班级列表 13.24/40.51 ms；50 人列表 48.73/69.82 ms；作业列表 65.07/88.18 ms；20 题详情 39.02/63.10 ms。没有采集 Worker 总时长、队列等待、查询数、慢 SQL、CPU、峰值内存和大文件大小。

## 修复与风险

- P0 已修：任意对象键可未认证读取元数据、签名或删除。
- P1 已修：学生作业缺少真实内容/页数/像素检查且解析前写对象。
- P1 已加固：Worker 崩溃重投和超时配置。
- P2 未修：Next.js 构建的 SWC lockfile 自动修补警告；构建仍成功。

外部依赖：RapidOCR 可用但不保证手写/公式；公式 OCR unavailable；真实主观题 AI unavailable。发布含义是教师确认版本，不是已发送学生端。

第二部分新增 BUSINESS-E2E 与机器证据，并更新 HANDOFF、FINAL-ACCEPTANCE 和能力矩阵。本任务没有 git add、提交、推送、PR 或部署；没有删除 Volume；独立 E2E 栈和证据数据保持运行/保留，等待用户决定。
