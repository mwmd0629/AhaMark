# 备份与恢复运行手册

## 适用范围与限制

本手册只适用于经过授权、具有明确 Run ID 的独立恢复环境。第七部分正式证据来自纯合成数据：

- Run ID：`gate-20260724-static-a1`
- Compose project：`ahamark-recovery-v7-gate-20260724-static-a1`
- PostgreSQL 逻辑备份恢复：开发环境 PASS
- MinIO 对象恢复与引用对账：开发环境 PASS

这不是生产灾备、高可用、SLA 或生产 RPO/RTO 证明。尚未验证异地备份、加密备份、密钥轮换、长期保留、增量备份、生产规模或多实例恢复。

## 身份守卫

所有命令必须显式设置本次 Run ID、Compose project、源和目标资源。占位符必须先替换，不能直接执行。

```powershell
$RecoveryRunId = '<1-32位小写字母、数字和连字符>'
$RecoveryProject = "ahamark-recovery-v7-$RecoveryRunId"
$SourceDatabase = "ahamark_recovery_$($RecoveryRunId.Replace('-', '_'))"
$TargetDatabase = "ahamark_recovery_restored_$($RecoveryRunId.Replace('-', '_'))"
$SourceBucket = "ahamark-recovery-source-$RecoveryRunId"
$TargetBucket = "ahamark-recovery-restored-$RecoveryRunId"

if ($RecoveryRunId -notmatch '^[a-z0-9-]{1,32}$') { throw 'invalid recovery Run ID' }
if ($RecoveryProject -ne "ahamark-recovery-v7-$RecoveryRunId") { throw 'project mismatch' }
if ($TargetDatabase -notlike "*$($RecoveryRunId.Replace('-', '_'))*") { throw 'target database mismatch' }
if ($TargetBucket -notlike "*$RecoveryRunId*") { throw 'target bucket mismatch' }
```

如果同名工作目录、Compose project、容器、卷、网络、数据库或 bucket 已存在，立即停止；不得复用、覆盖或清理。

## PostgreSQL 备份前检查

1. 记录源数据库精确名称、服务器版本和当前 Alembic revision。
2. 确认源数据库是授权的合成恢复源，不是默认业务数据库。
3. 确认目标数据库不存在；不能仅凭连接失败推断为空。
4. 记录关键表计数、关键 ID、complete/incomplete Snapshot 边界和稳定哈希。
5. 记录备份窗口内是否发生写入。无法确认时不能声称 RPO 为 0。

只读检查必须使用明确环境文件和 project：

```powershell
docker compose --env-file "<recovery-runtime-env>" -p "<recovery-project>" `
  -f docker-compose.recovery.yml exec -T api alembic current
```

## 一致性备份

使用 PostgreSQL custom 格式，备份文件保存到本次 `.recovery-v7/<run-id>/` 工作目录。备份命令必须显式指向已核验的源库：

```powershell
docker compose --env-file "<recovery-runtime-env>" -p "<recovery-project>" `
  -f docker-compose.recovery.yml exec -T postgres `
  pg_dump -Fc -U "<recovery-user>" -d "<verified-source-database>" -f "/tmp/<run-id>.dump"
```

记录开始/完成时间、文件大小和 SHA-256。不得把包含业务数据的 dump 提交到 Git。

## 恢复到全新独立数据库

目标必须是本轮全新空数据库。恢复使用：

- `--no-owner`
- `--no-privileges`
- `--single-transaction`

```powershell
docker compose --env-file "<recovery-runtime-env>" -p "<recovery-project>" `
  -f docker-compose.recovery.yml exec -T postgres_restore `
  pg_restore --no-owner --no-privileges --single-transaction `
  -U "<recovery-user>" -d "<verified-new-empty-target-database>" "/tmp/<run-id>.dump"
```

不得对未知目标使用清理式恢复，也不得覆盖现有业务数据库。任何目标身份或空库证明不明确时必须停止。

## Alembic revision 与业务关系对账

恢复后分别核验源库和目标库：

- Alembic revision 相同且为预期 head。
- 关键表计数、关键 ID 和稳定哈希一致。
- complete Snapshot 可用于最终成绩，incomplete Snapshot 不进入发布。
- 缺交和未 finalized 学生不记零。
- GradeRelease 固定具体 Snapshot。
- ReportJob 固定 GradeRelease。
- AnalyticsSnapshot 固定 GradeRelease。
- TeachingInsight 固定 AnalyticsSnapshot。
- StoredFile 动态业务外键不存在违规引用。

正式 Run 的源/恢复稳定哈希均为：

`57a4c1da9f9ef9b5a95f323aac49ea0cbd61b7ec555ea89651c9224ec4d83480`

## MinIO manifest 与空 bucket 恢复

1. 从源 bucket 生成包含对象键、大小、ETag、content type、更新时间和分类的 manifest。
2. 证明目标 bucket 不存在；如果存在则停止，不覆盖、不清空。
3. 创建本 Run 的新目标 bucket。
4. 按 manifest 逐对象复制；同名 key 已存在即停止。
5. 从目标端重新读取实际 metadata，不使用源 manifest 代替恢复后检查。

正式 Run 为 7 个源对象和 7 个目标对象，metadata/checksum/content-type 不匹配为 0。

## StoredFile、文件解析与孤儿分类

恢复后逐一核对 StoredFile 和动态业务引用，包括 ReportJob、PaperPage、SubmissionPage 及其他实际外键。对文件内容执行真实结构解析：

- PNG：3
- PDF：1
- XLSX：1
- ZIP：1
- JSON：1

孤儿扫描只读分类：

1. 数据库有记录但对象缺失
2. 对象存在但数据库无记录
3. 合法派生对象
4. 已知历史孤儿
5. 本 Run 未知孤儿
6. 无法分类

不得自动删除任何孤儿、bucket 或对象。正式 Run 只有 1 个合法派生对象，其余五类均为 0。

## 签名 URL 重新签发

恢复后必须重新签发短期 URL，不能复用备份前 URL。正式 test-only 验证使用 2 秒 TTL：

- 初次读取：200
- 到期后：403
- 重新签发：200
- 旧 URL 在重签后仍为 403

证据不得保存完整 URL、查询参数或 `X-Amz-*` 字段。

## 停止条件

出现以下任一情况立即停止：

- Run ID、project、数据库、bucket、卷或网络身份不一致。
- 目标数据库非空、目标 bucket 已存在或含对象。
- Docker API 返回错误或资源状态无法确认。
- Alembic revision、关键计数、稳定哈希或业务绑定不一致。
- 出现数据库缺对象、本 Run 未知孤儿或无法分类项。
- 发现凭据、真实个人信息或来源不明的破坏性逻辑。

不得执行 `docker compose down -v`、Docker prune、未知数据库 downgrade、覆盖非空 bucket 或自动删除孤儿。

## 证据与敏感信息

原始证据保存在被忽略的 `.recovery-v7/<run-id>/`，可能包含大量对象和记录级信息，不提交。正式提交只包含：

- [backup-restore-verification.json](backup-restore-verification.json)
- [failure-recovery-verification.json](failure-recovery-verification.json)

摘要不得包含密码、Token、Cookie、CSRF、密钥、完整签名 URL、查询参数、数据库/MinIO 凭据或真实个人信息。

## 开发机 RPO/RTO 观察值

本轮观察 RPO 为 0 秒，只因为备份窗口内源库没有写入；它不是生产 RPO 承诺。`2.314` 秒是独立数据库的 `pg_restore` 耗时，不是完整应用恢复时间，也不包含资源调度、应用启动、对象恢复、DNS、流量切换或人工决策。
