# AhaMark 最终验收报告

版本 `0.1.0`；更新日期 2026-07-28；分支 `master`；批改闭环最终功能基线为
`2377cd3`，包含线性代数批改第 1–4 部分；第 5 部分离线评测在本次收口完成。
本地未 push；本报告不构成生产能力声明。

本报告中的 `PASS` 仅表示对应验收检查在记录范围内通过，不等于整项能力已完成真实环境验证或达到生产可用。统一能力状态、数据边界和产品措辞以 `PROJECT-BASELINE.md`、`CAPABILITY-EVIDENCE-MATRIX.md`、`DATA-SECURITY-BOUNDARIES.md` 和 `PRODUCT-CAPABILITY-STATEMENTS.md` 为准。

## 最终结论

**等级 C：内部演示或开发测试。** 原定第一至第八部分均已正式关闭。教师核心业务 A–H、
第五部分权限/文件矩阵、第六部分开发机有界容量、第七部分 7A–7D 以及第八部分
8A–8E 和 Edge 已用纯合成数据通过。PostgreSQL/MinIO 独立恢复和单
Worker 故障恢复只在开发环境成立；生产灾备、高可用、生产 RPO/RTO、SLA、多实例恢复和
生产运维均未建立，不适合真实学生数据、真实教学试点、生产部署或公网开放。

## 验收矩阵

| 项目 | 状态 | 证据/说明 |
|---|---|---|
| 六核心服务 | PASS | web/api/worker/PostgreSQL/Redis/MinIO healthy；栈保持运行 |
| 数据库迁移 | PASS | current/heads 均为 `0024_nullable_publish_readiness_due_at`；本轮无迁移 |
| 后端测试 | PASS（定向范围） | 第三至第五部分定向集合 36 passed、1 warning；完整套件在 120 秒窗口未完成；Ruff format/check 167 files、mypy 91 files |
| 前端测试 | PASS | 完整 Vitest：21 files、60 tests passed |
| 前端格式/lint/type | PASS | Prettier、ESLint、TypeScript |
| Next production build | PASS | 23 条路由构建成功；SWC lockfile 修补警告仍存在但未阻断 |
| 线性代数离线评测 | PASS（安全模式） | 24 例；status accuracy 100%；false_verified 0；引用拦截和 manual/unsupported 遵从均 100% |
| 真实 Provider 质量 | NOT RUN | Provider 默认 unavailable；Fake 仅限测试；没有真实外部 API/学生文件上传 |
| Analytics HTTP | PASS | 35 请求，含 14 项 Teacher B 隔离 |
| Analytics 浏览器 | PASS | 6 步无头 Edge 冒烟 |
| 教师核心业务浏览器 A–H | PASS | 独立栈、无头 Edge、纯合成数据；8/8 阶段通过，见 BUSINESS-E2E 与机器 JSON |
| 全链路 API 集成 | PASS | 第三部分 5 项异常/版本测试通过；failed/expired ReportJob retry 另有真实 Edge 证据 |
| 完整权限矩阵 | PASS | 27 资源×29 操作；117 适用、666 N/A；身份结果 702/702 |
| 成绩正确性 | PASS | 第四部分金标准 v1/v2 全链路对账；Edge 12/12 含两类错误下钻、班级/学生趋势；未完成学生不记零 |
| XLSX/中文 PDF | PASS | 固定 Release 的 50 名不同学生：50 PDF、50 行 XLSX、含 50 PDF 的 ZIP；52/52 Celery/MinIO Job completed |
| 第六部分开发机容量 | PASS | 单 API/单 Worker；1600/1600 同步请求，Fake/RapidOCR 至 250 页，50 人报告及 200 人/100 题 Analytics 完成 |
| 生产容量与 SLA | PARTIAL | 最大规模 Analytics 学生趋势/详情约 7.7–7.9 秒；多实例、正式 SLA、故障条件和生产数据分布未验证 |
| MIME/恶意文件 | PASS | 41 个运行时结构 fixture；批次与存储/DB 回滚通过 |
| Auth/Session/CSRF | PASS | 登录、错误、Cookie、CSRF、过期、撤销、production 边界；Redis 共享限速在本地双 API 环境通过 |
| MinIO/签名 URL | PASS（开发范围） | owner 隔离；7/7 对象独立恢复；2 秒 test-only 到期 403、重签 200、旧 URL 仍失效 |
| Worker 故障 | PASS（开发范围） | 单 Worker 离线/崩溃、redelivery、Redis/MinIO 故障和恢复后对账通过 |
| 反向代理 | PASS | nginx -t、health、登录、Cookie、CSRF、安全头 |
| PostgreSQL 备份恢复 | PASS（开发范围） | custom-format 独立恢复，Alembic/计数/关系/稳定哈希一致 |
| MinIO 备份恢复 | PASS（开发范围） | 新空 bucket、7/7 对象、metadata/引用/解析/签名 URL/孤儿对账通过 |
| 第七部分 7A–7D | PASS | 两个正式 Run、两份原始证据和两份脱敏摘要；文档门禁通过 |
| 生产灾备 | NOT ESTABLISHED | 异地、加密、密钥轮换、长期、增量和生产规模未验证 |
| 生产高可用 | NOT ESTABLISHED | 第八部分仅证明本地双 API 故障切换；PostgreSQL、Redis、MinIO、Nginx 和 Worker 仍可能是单点 |
| 生产 RPO/RTO | NOT ESTABLISHED | RPO 0 秒因窗口无写入；2.314 秒仅为独立数据库恢复 |
| 可用性/可访问性逐页 | NOT RUN | Analytics 冒烟不能替代完整巡检 |
| 真实主观题 AI | NOT APPLICABLE | Provider unavailable；主观题人工评分；production 禁 fake |

## 批改闭环最终集成（第六部分）

- `4c6266b`：Structured Rubric 默认使用题目真实满分；`manual_only` 可在
  `validation_rule` 为空时绑定；集中审查过滤 stale/superseded，并将人工解决动作限制在白名单；
  隔离浏览器门禁有明确等待上限。
- `8746e18`：failed ReportJob 保持终态、重试创建新任务；XLSX 全部外部文本列统一使用公式注入
  防护。
- 验证闭环为：发布作业 → GradingBatch → 上传/匹配 → 页面处理/OCR → 答案确认 →
  客观规则/主观人工评分 → TeacherReview → finalize → complete Snapshot → GradeRelease →
  报告 → Analytics。
- AI/Codex 结果始终是 suggestion-only，教师拥有最终裁决；主观题真实 Provider unavailable，
  Fake Provider 仅限非 production 测试，不构成质量证据。OCR 工程链路通过不证明手写或公式准确率。
- 最终成绩只来自合法 complete `SubmissionScoreSnapshot`；`released` 只表示教师确认发布版本，
  不表示学生已收到。

## 第五部分权限与文件安全

5A、5B 正式关闭：27 类资源×29 类操作，117 个适用格、666 个明确 N/A，六种身份
702/702；隔离栈 HTTP 16/16；文件结构 fixture 41/41；本轮孤儿增量为 0。P0/P1 无
未修项。本结论不是外部渗透或生产安全认证，也不完成 Cookie 重放专项、
生产灾备、高可用、生产容量/SLA或生产运维；项目等级保持 C。

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

历史单客户端冒烟成功率均 100%。P50/P95：登录 45.48/82.33 ms；班级列表
13.24/40.51 ms；50 人列表 48.73/69.82 ms；作业列表 65.07/88.18 ms；20 题详情
39.02/63.10 ms。

第六部分关闭轮另行完成 1600/1600 同步请求、详情并发、Fake/真实 OCR 至 250 页、50 名
不同学生报告以及 200 人/100 题 Analytics。开发机有界容量为 PASS；最大规模 Analytics
学生趋势/详情约 7.7–7.9 秒，生产容量和 SLA 仍为 PARTIAL / NOT ESTABLISHED。部分精确
分段指标（例如连接池等待和序列化时间）未形成可靠机器证据，不影响上述功能与关闭门槛，
但生产压测仍需补齐。

## 修复与风险

- P0 已修：任意对象键可未认证读取元数据、签名或删除。
- P1 已修：学生作业缺少真实内容/页数/像素检查且解析前写对象。
- P1 已加固：Worker 崩溃重投和超时配置。
- P2 未修：Next.js 构建的 SWC lockfile 自动修补警告；构建仍成功。

外部依赖：RapidOCR 可用但不保证手写/公式；公式 OCR unavailable；真实主观题 AI unavailable。发布含义是教师确认版本，不是已发送学生端。

第二部分新增 BUSINESS-E2E 与机器证据，并更新 HANDOFF、FINAL-ACCEPTANCE 和能力矩阵。本任务没有 git add、提交、推送、PR 或部署；没有删除 Volume；独立 E2E 栈和证据数据保持运行/保留，等待用户决定。

## 第七部分恢复验收（2026-07-24）

状态：**7A–7D PASS，开发环境范围。**

- 正式 7A/7B Run：`gate-20260724-static-a1`
- 正式 7C Run：`fault-20260724-c84f19`
- 7A/7B 原始证据 SHA-256：
  `d2ea850fbcc8769ca875be1a9df5fd8745fd6730774f20bc050678fa7e0816a2`
- 7C 原始证据 SHA-256：
  `fe825009dd0f64ba9636c8244170c215181fa3f1da974dc574c46dd61c98ac5f`
- 正式摘要：`backup-restore-verification.json`、`failure-recovery-verification.json`
- 运维文档：`BACKUP-RESTORE.md`、`FAILURE-RECOVERY.md`、`OPERATIONS.md`

观察 RPO 为 0 秒，只因为备份窗口内没有源写入；不是生产承诺。2.314 秒只表示独立
PostgreSQL 数据库恢复，不是完整应用恢复时间。恢复 broker visibility timeout 为 15 秒，
但实际重投完成为 102.230 秒，15 秒不是实际恢复时间或 SLA。

Docker Desktop Engine 曾持续返回 HTTP 500，仅执行一次官方正常重启；未执行 WSL reset、
factory reset、prune 或数据清理。重启会中断全部本地容器，不能作为生产高可用机制。
7A/7B 容器因此 stopped；其 ID、5 个卷和网络保持不变。7C 最终 7 服务 healthy。

正式 Run 和所有失败 Run 均保留，失败 Run 不得拼接为 PASS。资源占用磁盘，后续清理必须
取得独立授权、列出精确目标并再次确认。该轮第七部分验收时未清理 Docker，第八部分当时
尚未开始；随后第八部分已正式关闭并提交。

## 第六部分容量更新（2026-07-23）

状态：**PASS（开发机有界容量）**。同步容量的原始基线和中间轮次均保留为 failed
历史问题证据；最终 `sync-capacity-optimized.json` 为 passed，详情并发 20 P95 为
184.78/199.72/348.81 ms；
Fake OCR 编排与真实 RapidOCR 均完成 150/200/250 或 100/150/250 页阶梯；50 名不同学生
的 PDF/XLSX/ZIP 内容核验通过；50/100/200 人 Analytics 和 20 路创建幂等通过。
第六部分可以关闭，但最大规模 Analytics 学生读取约 8 秒，且结果不代表生产容量；
项目等级仍保持 C。
> 第八部分 8A–8E 及 Edge 已由正式 Run `v8-final-20260725-c6568104` 和两份
> preproduction 机器证据关闭；历史 Run `v8-20260725-000100` 仍为 PARTIAL。项目等级仍为
> C，生产高可用和生产灾备仍为 NOT ESTABLISHED。原定八部分已经完成；任何后续工作属于
> 重新规划的可选扩展，不自动形成新的编号部分。
