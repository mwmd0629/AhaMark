# 第八部分安全审计（2026-07-22）

## 结论

状态：**PARTIAL**。修复 1 个 P0 与 2 类 P1 加固问题；现有认证、Analytics 隔离和新文件隔离回归通过。完整 23 类资源 × 操作矩阵、Cookie 重放、多实例限速、Redis/MinIO 故障和所有恶意文件 fixture 尚未执行，因此不支持真实教学试点或生产。

## 已修复

- **P0 任意文件访问/删除：** `/files/{key}` 元数据、删除和签名 URL 原先没有认证和 owner 校验。现在所有入口要求会话、CSRF（写操作）并用 `StoredFile.owner_id` 查询；跨教师统一 404。业务文件不能经通用删除入口删除。
- **P1 恶意学生作业：** 原实现只看扩展名/MIME，且解析前写 MinIO。现在统一真实内容检查、先全批验证、再写对象；限制 PDF 页数和图片像素。
- **P1 Worker 丢失恢复风险：** Celery 设置 late ack、worker lost 重投、30 分钟 hard limit、29 分钟 soft limit和 prefetch=1；OCR/Submission OCR/Report 任务显式启用 worker-lost 重投。

## 认证和会话

- PASS：scrypt 独立盐；随机数据库 Session；HttpOnly、SameSite=Lax；production 自动 Secure；CSRF 缺失为 403；退出撤销；过期拒绝；禁用用户拒绝；production 禁 demo actor；统一登录错误。
- PASS：令牌不在 URL 或 localStorage；请求日志仅含 method/path/status/request ID。
- PARTIAL：登录限速为单进程内存实现，多副本需迁移 Redis；Cookie 重放、多会话管理 UI、固定 Session 专项未完成。

## 隔离和对象存储

- PASS：Analytics 真实 HTTP 14 项 Teacher B 越权均 404；文件元数据、签名 URL 和删除跨教师自动化均 404。
- PASS：对象键随机且含 owner；签名 URL 默认 900 秒；Bucket 未通过应用公开；Nginx 不代理 MinIO 控制台。
- PARTIAL：完整资源 list/get/create/update/archive/retry/download/confirm/finalize 矩阵未跑；真实签名 URL 到期等待测试未跑。
- PARTIAL：只读孤儿扫描发现 1 条数据库记录缺对象（合成 PDF）和 1 个对象缺数据库记录（旧 integration smoke）；未自动删除，详见 `storage-orphan-report.json`。MinIO 对象备份恢复未执行。

## 代理和响应头

Nginx `nginx -t`、代理健康检查、登录、Cookie、缺失/正确 CSRF 均通过。已配置 CSP、nosniff、Referrer-Policy、Permissions-Policy、DENY frame、Host/协议/IP/request ID 转发、静态缓存和 API no-store。HSTS 只允许在生产 HTTPS 虚拟主机启用。
