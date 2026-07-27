# Assignment Generation Stage 6 Preproduction

本轮隔离拓扑复用 `docker-compose.preproduction.yml` 的定义，但每个 continuation Run 使用全新的 PostgreSQL、Redis、MinIO、
migrate、API-A、API-B、Worker、Web 与 Nginx/HTTPS Compose project，绝不修改冻结历史 Run。静态守卫
`scripts/assignment_generation_preproduction.py` 要求唯一
`ahamarkassignmentv6c<timestamp>` project、独立端口/三卷/网络/bucket 和新的
`synthetic.invalid` 账号 marker，拒绝旧 Stage 4 project
`ahamarkstage4ai233041`、旧 marker、端口复用以及任何 `down -v`、volume rm 或 system
prune 命令。

运行验收应验证 HTTPS、安全响应头、Secure Cookie、CSRF、production 禁 demo actor、
production fake 降级 unavailable、唯一 Alembic head、双 API、Worker readiness、
PostgreSQL/Redis/MinIO 和 Web。`/health` 只表示 API 进程存活且不探测依赖；`/ready` 以短超时探测 PostgreSQL、Redis 和 MinIO，任一不可用返回 503。Worker 是 soft/degraded component；OCR 和 Assignment Generation Provider 是 capability component，unavailable 不令 API 失去同步流量 readiness。Compose API healthcheck 使用 `/ready`，Nginx 对 502/503/504、连接错误和超时尝试另一 upstream。故障矩阵包括两个 API 单独停止、Worker 暂停恢复、
Redis/MinIO 短暂不可用、Provider unavailable/timeout/schema invalid、重复投递、取消与
单阶段重试、stale/教师编辑/晚到结果、readiness 后修改、双标签/双发布、Teacher B、
无 CSRF 和 Prompt Injection。

浏览器闭环从 HTTPS 登录、创建 draft、教师选班/上传/启动任务，经六阶段进度、基本信息与
文件角色/答案来源、页面与题目、答案/Rubric、集中审查、legacy binding、readiness 与教师
二次确认发布，并在刷新后恢复状态；发布后还要证明没有 GradeRelease、ScoreSnapshot 或
最终成绩。

Docker daemon 未运行时不得自动启动 GUI；静态和本地门禁继续，PREPRODUCTION、BROWSER
E2E 与 FAILOVER 保持 BLOCKED/PENDING。若 daemon 已运行，只能启动新的 Stage 6 project，
资源默认保留供审计，不触碰或清理 Stage 4。本轮不建立 PostgreSQL、Redis、MinIO 或 Nginx
高可用，不声明生产 HA、SLA、灾备、生产可用或真实教学可用。

AFFECTED DATABASE RECOVERY NOT PERFORMED。
