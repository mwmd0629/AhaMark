# 安全审计（更新于 2026-07-24）

## 结论

状态：**PASS（第五部分矩阵范围内）**。27 类资源 × 29 操作的显式适用/N/A
矩阵、六身份 702/702、41/41 文件 fixture、全业务路由 Session/CSRF 边界和隔离栈
真实 HTTP 16/16 已通过。第七部分另在纯合成独立开发环境完成 PostgreSQL/MinIO 恢复和
单 Worker 故障恢复；第八部分已验证 Redis 共享多实例限速和本地双 API 切换，但不改变
Cookie 重放、外部渗透、生产灾备与高可用
缺口。项目整体等级保持 C，不适合真实学生数据、真实教学试点、生产部署或公网开放。

## 已修复

- **P0 任意文件访问/删除：** `/files/{key}` 元数据、删除和签名 URL 原先没有认证和 owner 校验。现在所有入口要求会话、CSRF（写操作）并用 `StoredFile.owner_id` 查询；跨教师统一 404。业务文件不能经通用删除入口删除。
- **P1 恶意学生作业：** 原实现只看扩展名/MIME，且解析前写 MinIO。现在统一真实内容检查、先全批验证、再写对象；限制 PDF 页数和图片像素。
- **P1 Worker 丢失恢复风险：** Celery 设置 late ack、worker lost 重投、30 分钟 hard limit、29 分钟 soft limit和 prefetch=1；OCR/Submission OCR/Report 任务显式启用 worker-lost 重投。

## 认证和会话

- PASS：scrypt 独立盐；随机数据库 Session；HttpOnly、SameSite=Lax；production 自动 Secure；CSRF 缺失为 403；退出撤销；过期拒绝；禁用用户拒绝；production 禁 demo actor；统一登录错误。
- PASS：令牌不在 URL 或 localStorage；请求日志仅含 method/path/status/request ID。
- PARTIAL：登录限速已迁移到 Redis 共享固定窗口并在双 API 下验证；Cookie 重放、多会话管理 UI、固定 Session 专项未完成。

## 隔离和对象存储

- PASS：Analytics 真实 HTTP 14 项 Teacher B 越权均 404；第五部分隔离 HTTP 中
  StoredFile 元数据、签名 URL 和删除跨教师均 404。
- PASS：对象键随机且含 owner；签名 URL 默认 900 秒；Bucket 未通过应用公开；Nginx 不代理 MinIO 控制台。
- PASS：117 个适用格、666 个 N/A 已机器化；test-only 2 秒签名 URL 在真实 MinIO
  到期 403，重新签发通过。
- PASS（开发恢复范围）：正式 7A/7B Run 将 7 个 MinIO 对象恢复到全新 bucket，
  metadata/checksum/content-type、StoredFile 动态引用、图片/PDF/XLSX/ZIP 解析和六类
  孤儿对账通过；未自动删除任何对象。结论不外推到生产对象存储或长期备份。

## 第七部分恢复安全边界

- 恢复命令要求 `APP_ENV=test`、恢复门禁、合法 Run ID，以及数据库/bucket/project 的
  精确身份一致。
- Compose 无宿主机发布端口，源/目标数据库、bucket、卷和网络均独立命名。
- reconciliation 默认只读，不自动删除孤儿、bucket、对象或记录。
- 原始证据位于被忽略的 `.recovery-v7/`；正式摘要不含密码、Token、Cookie、CSRF、
  完整签名 URL、查询参数、`X-Amz-*` 或运行时凭据。
- test-only 故障检查点在 production 硬拒绝；只有 Celery 明确标记 redelivered 时才允许
  running Job 恢复。
- Docker Desktop Engine HTTP 500 仅通过一次正常重启恢复；未执行 WSL reset、factory
  reset、prune 或数据清理。桌面重启中断全部本地容器，不能作为生产高可用机制。
- 正式和失败 Run 均保留。失败 Run 只能作为诊断历史，不能拼接 PASS；任何资源清理需要
  独立授权、精确清单和再次确认。

## 代理和响应头

Nginx `nginx -t`、代理健康检查、登录、Cookie、缺失/正确 CSRF 均通过。已配置 CSP、nosniff、Referrer-Policy、Permissions-Policy、DENY frame、Host/协议/IP/request ID 转发、静态缓存和 API no-store。HSTS 只允许在生产 HTTPS 虚拟主机启用。
> 第八部分增量：production 集中启动拒绝、HMAC Session 摘要、Redis 共享固定窗口登录
> 限速（300 秒/5 次，故障 fail closed）及显式 CORS/Host/CSRF origin allowlist 已由正式
> Run `v8-final-20260725-c6568104` 验证；8A–8E 及 Edge 均 PASS。该结果不是外部渗透
> 认证，也不建立生产高可用或灾备。
