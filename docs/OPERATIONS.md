# 部署、备份恢复与排障

当前项目整体等级为 C，仅适合内部演示或开发测试。不适合真实学生数据、生产部署或公网
开放。第七部分恢复 PASS 只适用于独立纯合成开发环境；生产灾备、高可用、RPO/RTO、
多实例恢复、异地/加密/增量/长期备份均未建立。

## 线性代数 AI 批改离线评测与 Provider 门槛

所有文件处理、样本生成和结构化评测均在本地 Codex/离线代码完成，不上传文件、不调用真实
外部 API。运行：

```powershell
$env:PYTHONPATH='apps/api;.'
python scripts/linear_algebra_offline_evaluate.py data/linear_algebra_evaluation_v1.json `
  --output docs/linear-algebra-evaluation-v1-report.json
```

当前合成集为 24 例，覆盖全部线性代数 registry 类型、冲突/退化、域与资源边界、manual/
unsupported、伪造或 stale generation 引用。准入门槛是 `false_verified=0`、引用拦截率 100%、
manual/unsupported 遵从率 100%、状态准确率至少 95%，并需人工抽检、隐私、成本和延迟证据。
当前报告只证明本地确定性安全模式；`production_ready=false`，真实 Provider 仍不可用。

## 架构与启动

六个核心服务为 web、api、worker、PostgreSQL、Redis、MinIO；浏览器必须通过 Nginx proxy 统一入口访问。复制 `.env.example` 为 `.env`，替换全部 `change-me` 值，生产必须设 `APP_ENV=production`、`DEMO_ACTOR_ENABLED=false`、`AUTH_COOKIE_SECURE=true`、HTTPS Origin 和专用 Bucket。

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up --build -d
docker compose exec -T api alembic upgrade head
docker compose ps
```

代理本地 HTTP：

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d proxy
Invoke-WebRequest -UseBasicParsing http://localhost:8080/health
```

生产在 Nginx 前终止 TLS 或增加 443 server，证书从 secret store 挂载，不入库；只在 HTTPS 域启用 HSTS。生产不要暴露 API、Web、MinIO 9000/9001 的宿主端口，只暴露代理。当前 Compose 不发布 MinIO API 或控制台；签名 GET/HEAD 由 proxy 按 bucket 路径转发并保留原始 Host。该开发配置仍不能直接用于公网。

停止但保留卷：`docker compose stop`。禁止使用 `docker compose down -v`。

## 迁移

迁移前同时备份 PostgreSQL 和 MinIO。检查：

```powershell
docker compose exec -T api alembic current
docker compose exec -T api alembic heads
docker compose exec -T api alembic upgrade head
```

当前代码的唯一 head 应为 `0026_student_portal`；活动库只有在本次迁移完成后才应显示该 revision。回滚只能在名称
明确的非生产库验证；历史上已在独立库完成空库 `upgrade head` 与 `0010 -> 0009 -> 0010`，
该历史记录不表示当前活动库仍停留在 0010。

## 恢复操作边界

详细步骤见 [BACKUP-RESTORE.md](BACKUP-RESTORE.md) 和
[FAILURE-RECOVERY.md](FAILURE-RECOVERY.md)。第七部分只证明纯合成、独立开发环境中的
PostgreSQL、MinIO 和单 Worker 恢复；生产灾备、高可用和 RPO/RTO 均未建立。

### 备份前资源身份

- Run ID 必须匹配 `^[a-z0-9-]{1,32}$`。
- Compose project、数据库、bucket、工作目录、卷和网络均必须包含同一 Run ID。
- 源库不能是默认业务数据库。
- 必须通过数据库目录查询证明目标数据库不存在或为空。
- 必须通过 bucket API 证明目标 bucket 不存在；不能用“读取失败”代替证明。
- 同名资源已存在即停止，不复用、不覆盖、不主动清理。

备份文件和原始 manifest 可能包含敏感业务数据，只能保存在授权备份位置或被忽略的
`.recovery-v7/<run-id>/`。正式提交只包含脱敏摘要。

### PostgreSQL

使用 `pg_dump -Fc` 生成一致性逻辑备份。恢复只能进入名称明确、全新、独立的空数据库，
并使用 `--no-owner`、`--no-privileges`、`--single-transaction`。不得对未知目标使用
`pg_restore --clean`，不得对未知数据库执行 downgrade。

恢复后核验 Alembic revision、关键计数、关键 ID、稳定哈希、complete/incomplete
Snapshot、缺交/未 finalized 边界，以及 GradeRelease、ReportJob、AnalyticsSnapshot 和
TeachingInsight 的固定来源。

正式开发 Run `gate-20260724-static-a1` 的 PostgreSQL 备份恢复 PASS；观察 RPO 为 0 秒，
仅因为备份窗口内没有源写入。独立数据库恢复耗时为 2.314 秒，不是完整应用恢复时间，
也不是生产承诺。

### MinIO、StoredFile 与孤儿

从源 bucket 生成 manifest 后，只能恢复到本轮全新空 bucket。同名 key 已存在即停止，
不得覆盖。恢复后从目标端重新读取 size、checksum/ETag、content type，并实际解析图片、
PDF、XLSX 和 ZIP。

StoredFile 必须和 ReportJob、PaperPage、SubmissionPage 等动态业务外键对账。孤儿扫描
只读分类数据库缺对象、对象缺数据库、合法派生、已知历史、本 Run 未知和无法分类六类；
不得自动删除孤儿。

签名 URL 必须恢复后重新签发。证据不得保存完整 URL、查询参数或 `X-Amz-*`。

### Worker、Redis 与 MinIO 故障诊断

1. 先核验精确 project、Run ID 和容器标签；Docker API 错误与业务断言错误分开。
2. 检查 RecognitionJob/ReportJob 的 queued、running、failed 状态。
3. 检查 Celery `active` 和 `reserved`；不能仅凭数据库状态推断队列为空。
4. Worker 恢复 running Job 只允许在 Celery 明确标记 redelivered 时发生。
5. Redis 不可用应形成明确 `WORKER_UNAVAILABLE`，恢复后走受控业务 retry。
6. MinIO 报告写入失败后旧 ReportJob 保持 failed；retry 创建不同 ID 的新 Job。
7. failed、expired、partially_completed ReportJob 均通过 retry 新建 Job，不修改旧终态。

恢复 Compose visibility timeout 为 15 秒，但正式观察重投完成为 102.230 秒；Kombu
周期性恢复扫描意味着 15 秒不是实际恢复时间。production 默认仍为 3600 秒。

### 恢复后检查清单

- Alembic revision、计数、ID、关系和稳定哈希一致。
- queued/running 和 Celery active/reserved 均为 0。
- OCR Page/Block/Candidate、Report 幂等键和 AnalyticsSnapshot 无重复。
- complete Snapshot、GradeRelease、Report、Analytics、Insight 来源不漂移。
- 数据库缺对象、对象缺数据库、本 Run 未知孤儿、无法分类均为 0。
- 所有预期服务健康；Docker Engine 至少连续两次查询成功。

任一身份、目标空状态、数据关系、对象、队列或 Engine 状态无法确认时立即停止并保留现场。

### 明确禁止

- 禁止 `docker compose down -v`
- 禁止 Docker prune
- 禁止对未知数据库执行 downgrade
- 禁止 `pg_restore --clean` 作用于未知目标
- 禁止覆盖非空 bucket
- 禁止自动删除孤儿、未知对象或记录
- 禁止使用真实学生数据进行恢复演练
- 禁止将 Fake OCR 或 test-only 故障注入描述为生产能力
- 禁止删除当前正式或失败 Run、卷、网络、bucket

## 合成数据

最小 Analytics 数据：`python -m app.cli.seed_analytics_demo`；50 人数据：`python -m app.cli.seed_performance_demo`。分别使用 `analytics72.synthetic.invalid` 和 `performance50.synthetic.invalid` 固定 marker。清理必须传完全一致的 `--confirm-marker`；命令校验固定 ID/邮箱并打印范围，不使用 Bucket 通配符。

## 故障排查

- `/health` 只表示 API 进程；`/ready` 分别报告 PostgreSQL、Redis、Worker、MinIO、文字/公式 OCR。
- Worker 不可用时数据库状态是用户真相，不显示推测进度；检查 `docker compose logs --since 10m worker` 和 Job ID。
- 报告 failed/expired/partially_completed 使用 retry 创建新 Job，不修改旧终态。
- OCR/报告任务只携带 job ID，不携带文件、答案或 OCR 全文。
- 使用 `x-request-id`、Job ID、Submission ID、ReportJob ID 关联排障；不得记录密码、Cookie、CSRF、密钥、完整答案或成绩表。
- 公式 OCR 和真实主观题 AI Provider 均 unavailable；主观题必须人工评分，禁止 production FakeProvider。
> 本地预生产使用 `scripts/preproduction_v8.ps1` 和唯一 Run ID；仅 Nginx HTTPS 绑定
> `127.0.0.1`。正式 Run `v8-final-20260725-c6568104` 的 8A–8E 及 Edge 已 PASS，历史
> `v8-20260725-000100` 保持 PARTIAL。只证明本地 API 层切换；PostgreSQL、Redis、MinIO
> 和 Nginx 仍可能是单点。禁止 `down -v`、prune、删除卷或复用既有资源。
