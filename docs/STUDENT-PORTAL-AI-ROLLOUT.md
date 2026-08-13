# 学生端与 OpenAI 接入交付手册

本文说明本次新增能力、部署顺序、OpenAI 配置、安全门禁、验收方法和回滚边界。当前代码仍按仓库既有分级属于开发/内部验证版本；完成本文的人工事项与真实环境验收之前，不应承载真实学生数据。

## 1. 已实现范围

### 学生端

- 教师可为学生档案创建或绑定登录账号；学生登录后只允许访问 `/api/student/*` 与受保护文件下载入口。
- 学生可查看已发布且属于自己班级的作业，在截止时间前上传 PDF 或图片并提交；重复请求有幂等保护。
- 学生只可查看教师已正式发布给自己的成绩、反馈和错题；`score_only`、`feedback_only`、`internal_only` 的发布可见性由后端统一执行。
- 错题本支持两条路径：连续询问 AI，或将问题提交给教师人工复核。教师可维持原判、改分、要求补充信息；学生可补充后再次进入待审队列。
- 学生可请求基于其已发布成绩和已发布资源生成的 AI 学习分析。
- 学生可查看教师已发布且面向其班级的 PPTX、DOCX、PDF、图片或网络资源，并通过短期签名地址下载。

### 教师端

- 班级学生列表新增学生账号开通入口。
- 新增教学资源管理页，支持文件上传、外链、班级范围、发布和下架。
- 新增学生复核工作台，可查看题目、学生答案、发布时的分数/反馈和对话摘要，并记录处理结果。
- 教师改分会写入 `ScoreRevision` 审计记录，但不会覆盖历史发布快照；教师仍需重新定稿并创建新版本 `GradeRelease` 才会展示给学生。

### AI 接入

- 主观题建议、错题追问和学习分析共用服务端 OpenAI Responses API 客户端。
- 采用结构化输出校验、显式超时/重试、稳定错误码、请求/响应哈希、token 用量、尝试次数与 provider request id 审计。
- 请求设置 `store=false`；学生真实标识不会发送给模型，安全标识为服务端 HMAC 假名。
- 外部请求总开关默认关闭；API 密钥仅从服务端环境变量读取，不进入浏览器包、数据库或日志。
- AI 回复永远不能直接修改成绩、创建发布版本或替代教师复核。

## 2. 数据库升级

1. 先备份 PostgreSQL，并记录当前 Alembic revision。
2. 在与生产同版本的预生产 PostgreSQL 上执行：

   ```powershell
   .\.venv\Scripts\python.exe -m alembic current
   .\.venv\Scripts\python.exe -m alembic upgrade head
   .\.venv\Scripts\python.exe -m alembic current
   ```

3. 确认唯一 head 为 `0026_student_portal`，并验证升级后旧教师流程仍可用。
4. 在预生产备份副本上演练 `0026 -> 0025 -> 0026`。降级会删除本次新增表及其中数据，正式环境禁止在未备份、未停写时直接降级。

SQLite 不能用来证明本仓库迁移链可回滚，因为早期迁移使用 PostgreSQL `JSONB`；必须使用真实 PostgreSQL 演练。

## 3. 服务与依赖

系统正常运行至少需要：

- PostgreSQL：业务、审计、AI 作业与对话的唯一事实来源。
- Redis：Celery broker/result backend 及共享登录限流。
- MinIO：作业、教学资源及现有报告文件。
- API：FastAPI 服务。
- Worker：必须加载 `wrong_question_ai` 与 `student_learning_analysis` 两类新增任务。
- Web：Next.js 16 学生端和教师端页面；本地 Node.js 必须为 `>=20.19.0`。
- Nginx：提供 Web/API/对象下载的同源入口。Web 构建时
  通过 Nginx 部署时，proxy/预生产 Compose 会在 Web 构建阶段把
  `NEXT_PUBLIC_API_URL` 覆盖为空，不能重新写入 API 跨域地址；仅直接运行
  `npm run dev` 时使用 `.env` 中的 `http://localhost:8000`。

开发环境使用 `docker-compose.yml` 加 `docker-compose.proxy.yml` 启动统一入口；预生产使用 `docker-compose.preproduction.yml`。部署时必须先完成迁移，再启动 API/Worker/Web。升级后检查 Worker 日志，确认以下任务已注册：

- `ahamark.wrong_question_ai.run`
- `ahamark.student_learning_analysis.run`

浏览器不能直接访问容器地址 `minio:9000`。开发 Compose 将
`MINIO_PUBLIC_ENDPOINT` 设置为 `localhost:8080`，预生产设置为
`localhost:${PREPROD_HTTPS_PORT}` 并开启 `MINIO_PUBLIC_SECURE=true`；Nginx 只代理
`/${MINIO_BUCKET}/...` 下带签名的 GET/HEAD 请求，并保留原始 `Host` 供 S3 SigV4
校验。MinIO API 和控制台均不直接发布。若改用真实对象存储域名，必须同步更新
public endpoint、TLS、DNS 与 Nginx/网关规则，并执行一次浏览器下载冒烟。

当前 Next 16 使用内联 hydration/bootstrap 脚本，因此本地和预生产 CSP 暂时包含
`script-src 'unsafe-inline'`。这是已知安全债务：公网发布前应由应用和网关生成逐响应
nonce，改成 `script-src 'self' 'nonce-…'`，并移除 `unsafe-inline`；不能只删除该值而
不先完成 nonce 改造，否则页面 hydration 会被 CSP 阻断。

## 4. OpenAI API 配置步骤

### 4.1 准备服务端凭据

1. 在 OpenAI 项目中创建仅供此系统使用的项目级 API key，并使用平台可用的最小权限。
2. 将密钥保存到部署平台的 Secret 管理服务；不要写入 `.env.example`、Git、前端变量或镜像层。
3. 为试点环境设置独立项目/预算/告警，以便与生产用量隔离。

### 4.2 先保持熔断状态启动

先配置连接信息，但保持外部调用关闭：

```dotenv
AI_EXTERNAL_REQUESTS_ENABLED=false
SESSION_HMAC_SECRET=<由 Secret 管理服务注入的至少 32 位随机密钥>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=<由 Secret 管理服务注入>
OPENAI_PROJECT=<可选项目 ID>
AI_SAFETY_HMAC_SECRET=<独立的至少 32 位随机密钥，不得与会话密钥相同>

AI_GRADING_PROVIDER=openai
AI_GRADING_MODEL=<已在该项目开通、支持结构化输出的模型 ID>
AI_TUTOR_PROVIDER=openai
AI_TUTOR_MODEL=<已开通模型 ID>
STUDENT_LEARNING_PROVIDER=openai
STUDENT_LEARNING_MODEL=<已开通模型 ID>
```

模型名称会随账号权限和平台版本变化，部署者应从 OpenAI 项目实际可用模型中选择并固定明确 ID；不要从浏览器传模型名。首次部署建议先只开启错题答疑或学习分析中的一个功能，完成评测后再扩大范围。

### 4.3 设置容量与成本门槛

按数据规模调整但不要直接移除上限：

```dotenv
AI_TUTOR_TIMEOUT_SECONDS=45
AI_TUTOR_MAX_RETRIES=2
AI_TUTOR_MAX_INPUT_TOKENS=12000
AI_TUTOR_MAX_OUTPUT_TOKENS=2000
AI_TUTOR_MAX_CONVERSATION_MESSAGES=20
AI_TUTOR_MAX_QUESTIONS_PER_HOUR=30

STUDENT_LEARNING_TIMEOUT_SECONDS=60
STUDENT_LEARNING_MAX_RETRIES=2
STUDENT_LEARNING_MAX_INPUT_TOKENS=24000
STUDENT_LEARNING_MAX_OUTPUT_TOKENS=3000
STUDENT_LEARNING_MAX_GRADE_RELEASES=50
STUDENT_LEARNING_MAX_REQUESTS_PER_DAY=10
```

主观题 AI 还应配置每题/每批成本上限和模型 token 单价。费率必须由运维根据当前供应商价格人工维护；代码不会自行猜测价格。
`SESSION_HMAC_SECRET` 和 `AI_SAFETY_HMAC_SECRET` 必须分别生成、分别托管，不能复用；
预生产 Compose 会在缺失会话密钥时拒绝启动，开启真实 AI 时生产配置也会拒绝空的
AI safety 密钥。

### 4.4 无真实学生数据的连通性验收

1. 使用合成题目、合成学生和无个人信息的测试班级。
2. 启动 API 与 Worker，确认配置校验通过。
3. 把 `AI_EXTERNAL_REQUESTS_ENABLED` 改为 `true` 后重启 API 和 Worker。
4. 分别触发一次错题追问、学习分析和（若启用）主观题建议。
5. 验证结构化输出、超时、429、拒答、无效模型、Worker 重投和 broker 暂时不可用场景。
6. 检查错题 AI job、学习分析和主观题 Provider 调用均持久化请求/响应哈希、token、尝试次数、稳定错误码和 request id；业务表会按设计保留要展示的 AI 内容，但任何表和日志都不得记录 API key。
7. 评估内容质量、教师推翻率、单次成本、P95 延迟和敏感信息暴露风险，通过门禁后才允许小范围试点。

### 4.5 故障熔断

发生成本异常、输出质量下降、隐私事件或供应商故障时，将以下值恢复为 `false` 并滚动重启 API/Worker：

```dotenv
AI_EXTERNAL_REQUESTS_ENABLED=false
```

关闭后教师人工复核、学生提交、发布成绩和教学资源仍可工作；仅新 AI 请求会失败为可识别的配置错误。

## 5. 上线前人工业务配置

1. 由管理员创建教师账号，并关闭 `DEMO_ACTOR_ENABLED`。
2. 校对学生档案邮箱，再由教师逐个创建/绑定学生账号。临时密码只能通过受控渠道交付。
3. 新建学生账号首次登录后必须先修改临时密码；当前版本仍没有忘记密码自助恢复、邮箱邀请确认和账号撤销 UI。正式试点前需由管理员建立恢复/撤销流程，推荐最终由统一身份平台接管。
4. 教师需要将作业设为已发布并关联班级；只创建草稿不会出现在学生端。
5. 成绩必须经过定稿快照和 `GradeRelease` 发布；教师在复核中改分后必须创建新快照和新发布版本。
   本次升级前生成的旧快照没有固化题干和学生答案，不能安全用于新增错题问答；需要由教师重新定稿并发布后才会出现完整上下文。
6. 教学资源必须选择班级并显式发布。PPTX/DOCX 会进行压缩包、宏、ActiveX、嵌入对象和外链检查，PDF 会拒绝脚本、自动动作、附件与多媒体，但这些结构检查不能替代生产恶意软件扫描/CDR。
7. 配置真实域名、TLS、反向代理、`TRUSTED_HOSTS`、精确 CORS/CSRF allowlist 和 Secure Cookie；不得使用通配符。确认 MinIO public endpoint 指向同源下载代理或专用对象域名，而不是容器主机名；不要发布 MinIO 控制台。
8. 建立学生/监护人隐私告知、第三方 AI 数据处理依据、保留期限、删除流程和人工申诉规则，尤其要评估未成年人数据要求。
9. 设置 PostgreSQL/MinIO 备份、恢复演练、Redis/Worker 告警、OpenAI 预算告警和审计日志保留策略。

## 6. 验收清单

### 权限和数据可见性

- 学生无法调用教师、班级管理、批改、资源管理 API。
- 学生 A 不能读取学生 B 的作业、错题、对话、AI job、复核请求或资源签名地址。
- 未发布、`internal_only` 和不属于学生班级的数据不会泄露。
- `score_only` 不返回反馈；`feedback_only` 不返回分数或满分。

### 业务闭环

- 学生上传作业后出现一次提交，重复请求不产生第二批次。
- 错题可连续追问，AI job 可查询；同一对话不会并发生成两条回答。
- 学生可转人工；教师可要求补充；学生补充后重新回到待审。
- 教师改分留存 `ScoreRevision`，旧发布保持不变，新发布后学生才看到新结果。
- 资源下架后学生列表和签名下载都立即拒绝。
- 学习分析只引用最新的已发布成绩与已发布资源，源数据不变时不会重复生成。

### 工程门禁

```powershell
.\.venv\Scripts\python.exe -m ruff check apps/api/app/api/student_portal.py apps/api/app/integrations apps/api/app/ai_tutor apps/api/app/student_learning workers/tasks/wrong_question_ai.py workers/tasks/student_learning_analysis.py tests/test_student_portal.py tests/test_openai_student_features.py
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run test
npm.cmd --prefix apps/web run build
docker compose -f docker-compose.yml -f docker-compose.proxy.yml config --quiet
```

还应在真实 PostgreSQL、Redis、MinIO、两个 API 实例和至少一个 Worker 的预生产环境执行一次浏览器 E2E；单元测试不能替代该步骤。
E2E 必须从 Nginx 统一入口完成登录、Next hydration、API 写请求和至少一个包含空格/中文
文件名的签名下载，并确认返回 URL 的 host 与浏览器入口一致、MinIO 控制台不可达。

## 7. 性能提升路线

按优先级建议：

1. 将实时错题问答、批量学习分析、OCR/批改拆到独立 Celery 队列和 Worker 池，分别设置并发、超时与自动扩缩容，避免长任务阻塞交互请求。
2. 复用 OpenAI HTTP 连接，增加全局并发配额、指数退避抖动与熔断器；按任务复杂度路由模型，并对稳定源哈希复用结果。
3. 消除学生列表、错题和教师复核详情中的逐行查询；改为批量加载/投影查询，并用真实数据量检查 `EXPLAIN ANALYZE` 后再增加复合索引。
4. 所有增长型列表改用游标分页；学习分析输入按最近发布版本增量构建，对长对话先做受控摘要。
5. 大文件改为浏览器直传对象存储的分片签名上传，上传完成后异步扫描；下载通过 CDN/对象存储承载，API 只做授权与短签名。
6. 对已发布资源目录和学生首页做短 TTL 缓存，并在成绩发布、资源上下架和班级成员变化时精确失效。
7. 用 SSE 或 WebSocket 取代高频 job 轮询；至少实施带退避的轮询与 `Retry-After`。
8. 建立指标：API/数据库 P50/P95/P99、队列等待、AI 429/5xx/结构校验失败、token 与成本、教师推翻率、资源下载失败率；以压测结果设 SLO。

## 8. 回滚边界

- 功能级回滚优先关闭 `AI_EXTERNAL_REQUESTS_ENABLED`、下架资源、停止新增学生账号绑定，而不是删除数据。
- 应用版本回滚前确认旧代码是否能容忍新增表；数据库结构通常可以暂时保留。
- 数据库降级会删除本次新增的账号链接、对话、复核、资源和学习分析数据，只能在确认备份可恢复且业务停写后执行。
- 已发布成绩快照是审计事实，不应通过手工 SQL 覆盖或删除。
