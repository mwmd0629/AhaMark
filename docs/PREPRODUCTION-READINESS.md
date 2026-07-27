# 第八部分：本地预生产就绪

本门禁只证明本机、纯合成数据条件下的预生产式运行证据，不代表公网部署、生产高可用、灾备、外部渗透测试或真实 AI/OCR 能力。项目等级保持 C。

## 隔离与启动

使用 `scripts/preproduction_v8.ps1 -Action prepare` 创建唯一 Run ID、随机凭据和两天有效的本地自签名证书。所有运行材料位于被忽略的 `.preproduction-v8/<run-id>/`。随后使用脚本输出的同一 Run ID 执行 `-Action config`、`-Action up` 和 `-Action status`。不得执行 `down -v`、prune 或删除任何现有资源。

Compose project 为 `ahamark-preprod-<run-id>`，拥有独立的 PostgreSQL、Redis、MinIO 卷和默认网络。宿主机仅发布 Nginx HTTPS，且绑定 `127.0.0.1`；API、Web、PostgreSQL、Redis 和 MinIO 均不发布端口。

## 安全设计

- `Settings` 在应用导入、监听端口及写入数据库前拒绝不完整或危险的 production 配置。
- Session 和 CSRF 状态存储于共享 PostgreSQL；token 只以带部署密钥的 HMAC-SHA-256 摘要保存。
- 登录限速使用 Redis 固定窗口计数：默认 300 秒、5 次；key 为部署密钥 HMAC-SHA-256，TTL 等于窗口。Redis 不可用时 production fail closed（503）。
- Nginx 在两个 API 实例间轮询，失败节点 `max_fails=1/fail_timeout=3s`；单实例停止不改变 Session。
- request ID 仅接受 64 字符以内的 ASCII 字母、数字、`-_.`，否则由 API 生成 UUID；响应回传 `X-Request-ID`。
- JSON 日志不记录请求体、密码、Cookie、CSRF、Authorization、Session 摘要、MinIO 凭据、签名 URL 或上传内容。

## 判定边界

只有 `docs/preproduction-readiness-verification.json` 与 `docs/preproduction-browser-verification.json` 中 8A–8E 和 Edge 全部为 PASS，且完整门禁无 P0/P1，才可关闭第八部分。未运行或受环境限制必须标记 BLOCKED，不能推断通过。

## API health 与 readiness 语义

- `/health` 是轻量进程存活探针，不访问 PostgreSQL、Redis、MinIO、Worker、OCR 或 Provider；API 进程可响应时返回 200。
- `/ready` 是依赖型业务流量探针。PostgreSQL、Redis、MinIO 为 hard dependency，任何一项不可用时返回 503 和脱敏 component 状态；全部可用时返回 200。
- Celery Worker 作为 soft dependency 报告 available/degraded，避免仅因异步能力暂时不可用而移除仍能处理同步请求的 API。
- OCR 与 Assignment Generation Provider 是 capability 状态；fake/unavailable 或未配置会显示 degraded/unavailable，但不使 API 整体 unready。
- API 容器 healthcheck 使用 `/ready`。Nginx 在连接错误、timeout、502、503、504 时尝试另一 API upstream；单实例 dependency readiness 失败不会把 503 固定返回给客户端。

## 2026-07-24/25 运行时验收

Docker Desktop 正常启动后连续两轮资源计数稳定。该轮历史 Run 为 `v8-20260725-000100`，入口为
`https://localhost:9443`。8A、8B 已 PASS；8C 的端口、HTTPS 和安全头通过，但现有 Nginx 容器因
healthcheck 使用 `localhost` 命中未监听地址而显示 unhealthy，源码已改为 `127.0.0.1`，按不重建资源边界未
重建容器，因此 8C 为 PARTIAL。8D 因没有 production-safe 的完整 GradeRelease/ReportJob
业务数据而 PARTIAL；8E 因没有异步 Job 的 HTTP→Worker 关联证据而 PARTIAL。真实 Edge 的登录、刷新、核心页面、
Cookie、浏览器存储、request ID、单 API 故障切换和退出均 PASS，但 GradeRelease/Report 状态数据场景仍未运行。

历史保留：初始静态 Run `v8-20260724T153500Z` 曾因 daemon 未运行 BLOCKED，随后发现大写项目名不被 Compose
接受；`v8-20260724-235900` 因证书脚本兼容问题中止。两者均未清理或复用。

## 2026-07-25 最终关闭验收

旧 Run `v8-20260725-000100` 继续作为 PARTIAL 历史证据原样保留，未重建、复用、覆盖或清理。最终正式 Run
为 `v8-final-20260725-c6568104`，入口为 `https://localhost:9543`。

- 8C PASS：Nginx 容器为 healthy，实际 healthcheck 使用
  `https://127.0.0.1:8443/health`；HTTPS、Host allowlist、安全响应头和仅 Nginx 回环端口发布均通过。
- 合成数据由受控 `create_teacher` 管理员能力创建教师，随后全部业务数据经正式 HTTPS API、真实 Session 和
  CSRF 按正常顺序创建。两份清晰印刷体提交由 production 配置下的 RapidOCR Worker 处理；人工修正 OCR
  候选后只使用客观题评分。此流程不构成 OCR 准确率证明。
- GradeRelease `f0b515a6-5c7c-455a-b6a6-f078d5d325fe` 固定两份 complete
  ScoreSnapshot；缺交学生未计零分，也未进入 Analytics 分母。AnalyticsSnapshot
  `ded1e65a-20e8-4f3a-a5ff-ea55be3358b7` 的参与人数为 2、平均分为 7.5。ReportJob
  `83b7bb12-0371-4bf5-8654-a97ee09f6fe6` 由正式 API 创建并由 Worker 完成。
- 8D PASS：精确确认 API A 的 project/service 标签后，只 stop/start 原 API A 容器。真实 Edge 使用同一
  Session 在停止期间继续读取班级、作业、Release 及固定 Snapshot、Report、Analytics 和学生数据；数据前中后
  不漂移，Nginx 未持续 502，最终 API A/B 均 healthy。
- 8E PASS：request ID `cb46f3c31344593140a48432c693b1f0` 精确贯穿 Nginx、API、
  Celery headers 和 Worker；Worker 同时记录 task ID、ReportJob ID、task name、终态及耗时。Nginx、API
  A/B、Worker 完整日志的敏感模式与实际本轮秘密命中均为 0。
- Edge PASS：登录、刷新 Session、有数据 Release/Report/Analytics、缺交分母、Cookie 属性、空
  localStorage/sessionStorage、API A 停止和恢复、退出与旧 Session 拒绝均通过。

最终自动化门禁为 Ruff format/check、完整 mypy、25 个定向安全/关联测试、完整 Pytest（138 passed、2
skipped）、Prettier、ESLint、TypeScript、Vitest（26 passed）、Next production build、Compose 渲染、
Nginx 配置、JSON 解析及 `git diff --check` 全部通过。机器可读的 ID、状态和原始证据 SHA-256 见两份 JSON。

2026-07-25 当前工作树复核未重建或清理任何 Docker 资源。Nginx 的实际容器 healthcheck
`https://127.0.0.1:8443/health`、`nginx -t`、双 API 健康状态和 Celery ping 再次通过。复核通过正式
HTTPS API、真实 Session/CSRF 为同一固定 Release 新建并完成 ReportJob
`a79dcecb-800d-4ce7-84ad-31ca82e5deaa`；request ID
`78e71e0fa29d1d639c0ae988befadfda` 在当前保留的 Nginx、API A 和 Worker 日志中精确一致，
Celery task ID 为 `c8b7b1ed-5930-4c37-9cf1-de65acef5096`，Worker 终态 completed、耗时
156 ms。随后只 stop/start 同一个 API A 容器；真实 Edge 在同一 Session 下对班级、作业、固定 Release/
Snapshot、新 Report、Analytics 和学生数据的前、中、后读取均通过，API A 恢复 healthy。当前复核证据保存为
忽略目录内的 `business-evidence-current.json` 和 `edge-evidence-current.json`，SHA-256 记录在机器 JSON。

据此，第八部分 8A–8E 及 Edge 均可正式关闭，项目等级仍为 C。该结论不代表生产高可用、生产灾备、外部渗透
通过、真实主观题 AI 可用或手写/公式 OCR 可靠。原定八部分已经完成；任何后续工作属于重新规划的可选扩展，
不自动形成新的编号部分。
