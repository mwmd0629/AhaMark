# 部署、备份恢复与排障

## 架构与启动

六个核心服务为 web、api、worker、PostgreSQL、Redis、MinIO；可选 Nginx proxy 是入口层。复制 `.env.example` 为 `.env`，替换全部 `change-me` 值，生产必须设 `APP_ENV=production`、`DEMO_ACTOR_ENABLED=false`、`AUTH_COOKIE_SECURE=true`、HTTPS Origin 和专用 Bucket。

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose exec -T api alembic upgrade head
docker compose ps
```

代理本地 HTTP：

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d proxy
Invoke-WebRequest -UseBasicParsing http://localhost:8080/health
```

生产在 Nginx 前终止 TLS 或增加 443 server，证书从 secret store 挂载，不入库；只在 HTTPS 域启用 HSTS。生产不要暴露 API、Web、MinIO 9000/9001 的宿主端口，只暴露代理。当前 Compose 是开发配置，MinIO 控制台仍绑定 localhost 的所有接口，不能直接用于公网。

停止但保留卷：`docker compose stop`。禁止使用 `docker compose down -v`。

## 迁移

迁移前同时备份 PostgreSQL 和 MinIO。检查：

```powershell
docker compose exec -T api alembic current
docker compose exec -T api alembic heads
docker compose exec -T api alembic upgrade head
```

回滚只能在名称明确的非生产库验证。本次已在独立库完成空库 `upgrade head`、`0010 -> 0009 -> 0010`。

## PostgreSQL 备份与恢复

备份文件包含敏感业务数据，不入 Git。示例在受控备份主机运行：

```powershell
docker compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" -f /tmp/ahamark.dump'
docker cp ahamark-postgres-1:/tmp/ahamark.dump C:\secure-backups\ahamark.dump
```

恢复必须先到独立数据库，再验证 users/classes/assignments/submissions/submission_score_snapshots/grade_releases 和对象引用。本次逻辑恢复验证计数为 3/4/5/8/8/3，未覆盖 MinIO 恢复。

MinIO 应使用 `mc mirror --version-id` 或对象存储原生版本化/生命周期策略备份到独立目标；恢复后运行：

```powershell
docker compose exec -T api python -m app.cli.scan_storage_orphans --output /tmp/storage-orphans.json
```

该命令只读列出“数据库有记录但对象缺失”和“对象存在但数据库无记录”，绝不自动删除。任何删除需逐项审批。

## 合成数据

最小 Analytics 数据：`python -m app.cli.seed_analytics_demo`；50 人数据：`python -m app.cli.seed_performance_demo`。分别使用 `analytics72.synthetic.invalid` 和 `performance50.synthetic.invalid` 固定 marker。清理必须传完全一致的 `--confirm-marker`；命令校验固定 ID/邮箱并打印范围，不使用 Bucket 通配符。

## 故障排查

- `/health` 只表示 API 进程；`/ready` 分别报告 PostgreSQL、Redis、Worker、MinIO、文字/公式 OCR。
- Worker 不可用时数据库状态是用户真相，不显示推测进度；检查 `docker compose logs --since 10m worker` 和 Job ID。
- 报告 failed/expired/partially_completed 使用 retry 创建新 Job，不修改旧终态。
- OCR/报告任务只携带 job ID，不携带文件、答案或 OCR 全文。
- 使用 `x-request-id`、Job ID、Submission ID、ReportJob ID 关联排障；不得记录密码、Cookie、CSRF、密钥、完整答案或成绩表。
- 公式 OCR 和真实主观题 AI Provider 均 unavailable；主观题必须人工评分，禁止 production FakeProvider。
