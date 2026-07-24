# 异步任务与基础设施故障恢复

## 验证结论与范围

第七部分 7C 正式 Run `fault-20260724-c84f19` 在纯合成、单 API、单 Worker 开发环境完成 12 个场景，结果 PASS。该结果不证明多实例高可用、生产容灾、生产 SLA 或生产 RPO/RTO。

正式摘要见 [failure-recovery-verification.json](failure-recovery-verification.json)，原始证据保存在被忽略的 `.recovery-v7/fault-20260724-c84f19/`。

## Worker 离线与崩溃

- Worker 离线期间 RecognitionJob 保持 `queued`；Worker 恢复后完成。
- Worker 在 `running` test-only 检查点被终止，数据库保持 running 真相。
- 同一 Celery task 被真实重投，Job attempt 从 1 变为 2，最终 completed。
- 只有 Celery `delivery_info.redelivered` 明确为真时，running Job 才允许恢复；普通重复派发不能恢复 running Job。

恢复后 OCR Page、Block、Candidate 重复数均为 0。

## Visibility timeout 与实际延迟

恢复 Compose 的 broker visibility timeout 为 15 秒；production 默认仍为 3600 秒。15 秒表示消息最早可重新可见，不是实际恢复时间。

正式 Run 从 Worker 崩溃到重投任务完成的观察值为 **102.230 秒**。延迟包含 Kombu 周期性恢复扫描和任务完成时间，因此不得把 15 秒写成实际恢复时间或 SLA。

## Redis 不可用

Redis 停止时，新建 RecognitionJob 明确返回 HTTP 503 和 `WORKER_UNAVAILABLE`，业务错误与 Docker Engine 错误分开记录。Redis 恢复后通过受控 retry 完成任务；Engine/API 错误不能被解释为业务故障通过。

诊断顺序：

1. 核验精确 Compose project 和 Run ID。
2. 检查 Redis 容器状态与健康检查。
3. 检查 Job 的 queued/running/failed 状态。
4. 检查 Celery active/reserved。
5. Redis 恢复后仅通过业务 retry 入口重试。

## MinIO 报告失败与 retry

MinIO 在 ReportJob 已进入 running 后停止，对象写入失败使旧 Job 进入 failed，且没有 StoredFile。MinIO 恢复后 retry 创建不同 ID 的新 ReportJob；旧 failed Job 保持不变，新 Job completed 并拥有 StoredFile。

不得把 retry 描述为修改或复活旧 Job。failed/expired/partially_completed 报告必须创建新 Job。

## 幂等验证

- Recognition：顺序和并发重复派发不增加 Page、Block 或 Candidate。
- Report：顺序 5 次、并发 20 次及重复任务派发只对应一个幂等 Job。
- Analytics：同一 GradeRelease 顺序 5 次和并发 20 次均只对应同一个 AnalyticsSnapshot。
- complete ScoreSnapshot、GradeRelease 和 TeachingInsight 绑定哈希不漂移。

## 最终对账

正式 Run 最终：

- RecognitionJob queued/running：0/0
- ReportJob queued/running：0/0
- Celery active/reserved：0/0
- Report idempotency key 和 AnalyticsSnapshot 重复：0
- 数据库记录缺对象、对象缺数据库记录：0
- 本 Run 未知孤儿、无法分类：0
- 7 个服务全部 healthy

孤儿只读扫描不得自动删除对象或记录。

## Test-only 故障注入边界

允许的检查点只有 `recognition-running` 和 `report-before-storage`。启用检查点必须同时满足：

- `APP_ENV=test`
- `RECOVERY_V7_ENABLED=true`
- Run ID、数据库和 bucket 通过恢复环境身份守卫

production 环境硬拒绝这些检查点。Fake OCR 与故障注入只用于自动化和恢复验证，不能描述为生产能力。

## Docker Desktop Engine HTTP 500

7C 前 Docker Linux Engine 持续对 `/version` 和 `/containers/json` 返回 HTTP 500。只执行了一次 Docker Desktop 官方正常重启：

- 开始：`2026-07-24T18:26:06.505+08:00`
- 连续健康门禁完成：`2026-07-24T18:27:57.183+08:00`
- 未执行 WSL reset、factory reset、prune 或数据清理
- 重启后 7A/7B 正式容器 ID、5 个卷和网络 ID 保持不变

Docker Desktop 重启会中断所有本地容器；7A/7B 正式容器因此保持 stopped，未被主动重建。生产环境不能依赖桌面应用重启作为高可用或容灾机制。

## 运维停止条件

以下情况必须停止并保留现场：

- Docker API 500 或 Engine 状态无法连续确认。
- 正式容器、卷、网络或 Run 标签缺失。
- redelivery 未明确标记、任务永久 running 或队列无法清空。
- 旧 failed Job 被修改，或 retry 未创建新 Job。
- 出现重复业务记录、对象缺失、未知孤儿或无法分类项。
- 需要扩展到 WSL reset、factory reset、prune、删除卷/网络/bucket。

失败 Run 只能作为诊断历史，不能拼接成 PASS。
