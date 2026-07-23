# 安全审计（含第五部分权限与文件安全，更新于 2026-07-23）

## 结论

状态：**PASS（第五部分矩阵范围内）**。27 类资源 × 29 操作的显式适用/N/A
矩阵、六身份 702/702、41/41 文件 fixture、全业务路由 Session/CSRF 边界和隔离栈
真实 HTTP 16/16 已通过。Cookie 重放、多实例限速、外部渗透与 Redis/MinIO 故障不在
本结论内，仍不支持真实教学试点或生产。

## 已修复

- **P0 任意文件访问/删除：** `/files/{key}` 元数据、删除和签名 URL 原先没有认证和 owner 校验。现在所有入口要求会话、CSRF（写操作）并用 `StoredFile.owner_id` 查询；跨教师统一 404。业务文件不能经通用删除入口删除。
- **P1 恶意学生作业：** 原实现只看扩展名/MIME，且解析前写 MinIO。现在统一真实内容检查、先全批验证、再写对象；限制 PDF 页数和图片像素。
- **P1 Worker 丢失恢复风险：** Celery 设置 late ack、worker lost 重投、30 分钟 hard limit、29 分钟 soft limit和 prefetch=1；OCR/Submission OCR/Report 任务显式启用 worker-lost 重投。

## 认证和会话

- PASS：scrypt 独立盐；随机数据库 Session；HttpOnly、SameSite=Lax；production 自动 Secure；CSRF 缺失为 403；退出撤销；过期拒绝；禁用用户拒绝；production 禁 demo actor；统一登录错误。
- PASS：令牌不在 URL 或 localStorage；请求日志仅含 method/path/status/request ID。
- PARTIAL：登录限速为单进程内存实现，多副本需迁移 Redis；Cookie 重放、多会话管理 UI、固定 Session 专项未完成。

## 隔离和对象存储

- PASS：Analytics 真实 HTTP 14 项 Teacher B 越权均 404；第五部分隔离 HTTP 中
  StoredFile 元数据、签名 URL 和删除跨教师均 404。
- PASS：对象键随机且含 owner；签名 URL 默认 900 秒；Bucket 未通过应用公开；Nginx 不代理 MinIO 控制台。
- PASS：117 个适用格、666 个 N/A 已机器化；test-only 2 秒签名 URL 在真实 MinIO
  到期 403，重新签发通过。
- PASS（本轮范围）：第五部分安全 marker 的孤儿增量为 0。历史只读报告中的既有
  缺对象/无记录项未自动删除；全局扫描还会把未建 StoredFile 的 recognition 派生图
  列为对象无记录。MinIO 对象备份恢复仍未执行。

## 代理和响应头

Nginx `nginx -t`、代理健康检查、登录、Cookie、缺失/正确 CSRF 均通过。已配置 CSP、nosniff、Referrer-Policy、Permissions-Policy、DENY frame、Host/协议/IP/request ID 转发、静态缓存和 API no-store。HSTS 只允许在生产 HTTPS 虚拟主机启用。
