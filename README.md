# AhaMark

AhaMark 是面向教师的作业整理、主观题批改与成绩分析系统。核心原则是：自动化只生成候选或建议，正式题目、答案、评分标准和成绩均由教师确认。

> 新接手任务先读[“接手必读：当前状态、问题与下一步”](#接手必读当前状态问题与下一步)。它是仓库唯一状态账本；历史细节以 Git 提交和 `docs/` 中的验收材料为准。

## 目录

- [接手必读：当前状态、问题与下一步](#接手必读当前状态问题与下一步)
- [产品能力与边界](#产品能力与边界)
- [系统结构](#系统结构)
- [快速开始](#快速开始)
- [账号与登录](#账号与登录)
- [教师主流程](#教师主流程)
- [OCR、公式与 AI 边界](#ocr公式与-ai-边界)
- [成绩发布与分析](#成绩发布与分析)
- [测试与质量门禁](#测试与质量门禁)
- [部署与运维](#部署与运维)
- [文档索引](#文档索引)
- [精简变更账本](#精简变更账本)

## 接手必读：当前状态、问题与下一步

### 30 秒摘要

| 项目              | 当前事实（2026-08-18）                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| 正确工作区        | `C:\Users\Lenovo\.codex\worktrees\06f7\AhaMark`                                                              |
| 目标分支          | `codex/integrate-question-page-cutter`                                                                       |
| 应用基线          | `9b129bc43961d296642b6fcb6cb461907f70a367`；后续 README 与合并门禁修复不改变运行逻辑                         |
| 远端状态          | 本地与 `origin/codex/integrate-question-page-cutter` 为 `0 ahead / 0 behind`                                 |
| 数据库迁移        | Alembic 单 head：`0049_usernames`                                                                            |
| 最新开发          | 全离线公式 OCR 与本地 Qwen 建议服务已以 `de22415` 提交并推送；尚未上传或部署，node2 仍保持旧版           |
| node2 在线版本    | API/Web/Worker 为 `5eda608`，schema 为 `0048_class_resources`；旧邮箱登录和文字 OCR available                |
| node2 入口        | `https://222.195.89.236:13300`；自签名证书；公网可达，无来源白名单                                           |
| 部署范围          | 只发布 Nginx `0.0.0.0:13300 -> 8443`；数据库、Redis、MinIO、API、Web、Worker、Docker socket 均无宿主发布端口 |
| 私有识别工作      | 暂停；不得继续处理、上传或提交私有 OCR/Gold、图片、正文或来源映射，除非用户再次明确授权                      |
| GitHub 合作者任务 | 已交由另一 Codex 任务处理；本任务不继续合并候选代码                                                          |

### 当前开发事实

2026-08-18 用户要求由 Codex 全程完成、不接第三方在线 Provider，并授权继续开发。当前工作树已接入两个只在 `local-ai` Compose profile 中启用的内网服务：固定 `PaddlePaddle/PP-FormulaNet_plus-M` 公式模型（revision `712e6e2e4c313b1ea163be5c350127b82662c58d`）和固定 `Qwen/Qwen3-4B-GGUF` 的 `Qwen3-4B-Q4_K_M.gguf`，由官方 llama.cpp CPU server 提供 OpenAI-compatible JSON Schema 接口。两者均不发布宿主端口，运行时不下载模型，模型卷只读；应用只允许显式 host allowlist 的 Compose HTTP，拒绝 IP、metadata host、localhost 和未授权外部端点。评分、Stage 4 AI grading 与作业生成只产出 suggestion，继续要求教师复核，外部 Provider 请求在 node2 Compose 中固定关闭。

模型获取脚本只接受新建或空目录，固定 URL、revision、大小和 SHA-256，下载到 `.part` 后验签再发布。公式三文件 SHA-256 分别为 `8333a7f650766a748e273c550d278601dd19dfeee1c4b01038ff632f134d9884`、`f16ef9b5c8227da70d3ec969a5195f4d62c1154427b883f4d6cff07633654041`、`87b5f3d7f2b2fe553627d77b37f496608ca150ebd0ef62d362591edca47b5538`，生成清单 SHA-256 为 `19bb16d0ba17771ce24dfce716d9f10f80c3df626ecc9b960283e28810190018`；Qwen GGUF SHA-256 为 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`。本地固定资产获取已通过。公式候选镜像在 `--network none`、只读根文件系统和只读模型挂载下 readiness 为 200，合成公式图片经真实 HTTP 推理返回 LaTeX 候选并带 `UNCALIBRATED_CONFIDENCE`、`TEACHER_REVIEW_REQUIRED`；这不是准确率。固定 digest `ghcr.io/ggml-org/llama.cpp:server@sha256:092d1291f2bcf59ff727fa3af855fb9bd4759d6bff860f6fbfd5e3e377e12625` 在同样无外网条件下加载 Qwen，健康检查为 200，显式关闭 Qwen3 thinking 后 JSON Schema 请求成功，实测容器内存约 2.35 GiB。全仓 Ruff、strict mypy（128 个源文件）、Compose 默认/`local-ai` 双配置通过；全部 120 个后端测试文件按固定排序分为三个独立 TEMP/数据库组，合计 `1062 passed, 19 skipped`（1081 项）、零失败，三组均为 `ahamark.db unchanged`。前端 Prettier、ESLint、TypeScript、`38 files / 234 tests` 与 production build 19 页通过，只有不阻断构建的既有 SWC lockfile 修补警告。实现已以 `de22415` 提交并推送；提交哈希候选镜像均已构建：API `sha256:32eb9b73409eec61a944565960baedefa211816b0b65689a4d1293a9245a10b4`（188836865 bytes）、Web `sha256:36e19e70ee8261452b16a0584d63b32db62062ea375eccd74be01d51cb08bdd5`（66252967 bytes）、Formula `sha256:5e13e614dd7c225cb01c82bec685a35b046d627ce6d6c0a535375b43617a827b`（542075507 bytes），llama.cpp 固定镜像为 `sha256:092d1291f2bcf59ff727fa3af855fb9bd4759d6bff860f6fbfd5e3e377e12625`（310736848 bytes）；API 镜像迁移头为 `0049_usernames`。node2 硬件清单 SSH 在认证前被远端关闭，尚未取得；本段模型和镜像尚未上传或部署，node2 三项 Provider 状态没有改变。

2026-08-18 已获用户授权自主完成后续开发与部署。production-safe RapidOCR bundle 接线已经完成本地开发和验证：默认 API 镜像继续关闭 OCR，node2 专用 `Dockerfile.rapidocr` 固定 `rapidocr==3.9.2`、`onnxruntime==1.28.0`，并把 wheel 内三份 ONNX 复制到固定目录；清单固定模型路径、大小、SHA-256、运行时版本、bundle/license approval UUID，清单 SHA-256 为 `f84336fc78cb51cd0ee223ee3c04158eb2f968af6fa8ffd31051b821f843ff5b`，NOTICE 明确仅批准本地印刷体文字 OCR。运行时下载仍被配置和代码双重禁止，启动/readiness 校验清单与模型，推理前再次检查文件身份，异常稳定 fail-closed。node2 Compose 的启用参数来自 `runtime.env` 且默认关闭，旧 `runtime.env` 与 `5eda608` 回滚路径保持兼容。

真实候选镜像在 `--network none` 下用合成印刷体图片完成一次离线推理：readiness 为 true，返回 2 个文字块并识别出 `AhaMark` 与 `123`；这只证明固定镜像链路可运行，不是准确率。该验证同时发现 RapidOCR v3 的 boxes 为 NumPy 数组，适配器现以有界形状检查后转换，并新增回归测试。后端全量唯一计数为 `1052 passed, 19 skipped`，零真实失败；三组初跑的 15 个失败均为外置 `--basetemp` 被数据库安全守卫拒绝，修正进程 `TEMP/TMP` 后相关 10 文件 `50 passed, 1 skipped`，所有运行均为 `ahamark.db unchanged`。全仓 Ruff、strict mypy（127 个源文件）、Alembic 单 head `0049_usernames` 通过；前端 Prettier、ESLint、TypeScript、`38 files / 234 tests` 和 production build 19 页通过；node2 Compose 在 OCR 默认关闭和显式固定 bundle 开启两种配置下均通过 `config --quiet`。实现已以 `789a59d` 提交并推送；node2 当前仍为 `5eda608 + 0048`，本段代码尚未部署。

2026-08-18 尝试部署 `056f039`：镜像双层 SHA-256、Compose、Nginx、`0048 -> 0049` 迁移、用户名回填和新 API-A/B 健康均通过；既有 1 个用户回填后 username 空值和重复数均为 0。切换后公网 `/ready` 暴露文字 OCR 从 available 降为 unavailable，因此按失败门禁将 API/Web/Worker 和 `runtime.env` 滚动恢复为 `5eda608`，公网 `/`、`/ready`、1 个 Worker 和文字 OCR 随后恢复 available，登录页也恢复旧邮箱界面。经用户单独授权并在新建、验证 `0049` PostgreSQL 备份后，数据库已事务性 downgrade 到 `0048_class_resources`，`users.username` 列确认不存在，旧 `migrate` 容器以 0 退出；node2 已完整恢复原应用/schema 基线。

根因不是简单漏装依赖：`056f039` 的生产 `provider_from_settings()` 对 RapidOCR 不接入 engine factory；仓库虽有 artifact/runtime 离线验证组件，但没有 production bundle wiring，符合“RapidOCR runtime/download hard-off”边界。曾构建一个 `.[ocr]` 本地候选，确认只装包仍不能恢复 Provider 后已撤销 Dockerfile 改动，候选未上传、未部署、未提交。恢复文字 OCR 需要单独设计经审计的固定 artifact 接线和测试，不能在服务器临时安装或启用运行时下载。

2026-08-18 随后对 `789a59d` 执行受保护部署。上传归档和候选 Compose 完整；服务器独立只读诊断确认 API/Web 镜像 ID 分别为 `sha256:1fa11b99a46d38ee2b1048d937537c6cc2d543258871b605f48cf52bf9d81d6b`、`sha256:acd8f0e9f8d7ea5c08f0b35ff9479b93ef5bff0f99d6eba4f8407e6fbd5abd80`，镜像内 head 为 `0049_usernames`，候选 runtime 明确启用固定 OCR，Compose 解析出的应用镜像均为 `789a59d`。部署自动化先后暴露 SSH stdin、Windows CRLF 与 Compose 子进程继承输入造成的提前结束；修复为服务器临时文件执行后，最终尝试仍在脚本 `image_validation` 阶段中止。最终尝试没有进入 `0048 -> 0049` 迁移，自动回滚确认数据库仍为 `0048_class_resources`、应用仍为 `5eda608`。按操作约定已停止继续尝试，不能把 `789a59d` 描述为已部署。

管理员账号登录已经从邮箱改为用户名：

- `User.username` 唯一、索引化，登录时执行 NFKC、转小写和格式校验。
- 用户名为 3–64 位，以字母或数字开头，只允许小写字母、数字、点、下划线和连字符。
- production `/auth/login` 只接受用户名；邮箱载荷返回统一认证失败，不泄漏账号是否存在。
- Web 登录页只显示用户名和密码，不提供公共注册、自助找回或账号申请。
- 教师和学生账号由服务器交互式 CLI 创建；密码输入两次且不回显，数据库只保存 scrypt 哈希。
- 迁移 `0049_usernames` 使用与邮箱无关的确定性占位用户名回填既有账号，避免泄漏邮箱。

本轮认证开发的验证结果：认证专项 `7 passed`，登录页 Vitest `1 passed`；Ruff、strict mypy（127 个源文件）、Prettier、目标 ESLint、TypeScript、Alembic 单 head 和 `git diff --check` 均通过；`ahamark.db` 不存在且未变化。

2026-08-17 合入 `master` 前的独立门禁发现并修正两处用户名提交遗留的测试契约：AI worker 迁移链测试仍把当前 head 写死为 `0048_class_resources`，现显式验证 `0049_usernames -> 0048_class_resources`；用户名迁移测试的两处常量 `setattr` 改为等价模块属性赋值，以满足 Ruff B010。认证、0049 升降级、AI worker 迁移契约、报表迁移和 orchestrator 模型链联合为 `31 passed, 4 skipped`，数据库守卫为 `ahamark.db unchanged`；全仓 Ruff lint、strict mypy（127 个源文件）和 Alembic 单 head `0049_usernames` 通过。前端 Git 跟踪文件 Prettier、ESLint、TypeScript、`38 files / 234 tests` 通过，Docker production build 和清理忽略的本地 pnpm/Next 产物后的 Windows 原生 production build 均成功生成 19 页；原 symlink 权限错误和多 lockfile 警告已消失，Next 仍有不阻断构建的 SWC lockfile 修补警告。2026-08-18 将全部 117 个后端测试文件分为三个互不重叠的隔离组并使用独立 `--basetemp` 完整重跑，合计 `1048 passed, 19 skipped`（1067 项）、零失败，三组数据库守卫均为 `ahamark.db unchanged`。全仓 Ruff format-check 仍只报告 12 个历史文件的既有 CRLF/新版 formatter 机械差异，本轮未接受无关大格式化。

本地已构建但未上传的镜像：

- `ahamark/api:9b129bc`：`sha256:394396195bed52243a7cc9638df3450609e73e53f96e1403a387becf8777be3f`
- `ahamark/web:9b129bc`：`sha256:d83b5a3db58347ec83b91e459e17d2f26ab73c98f66eedec741a976f72fe9688`
- 归档 SHA-256：`39a634a70db84837adb6107a38dad3dd8df4909812fb7700952cf72d8dec5f3a`

### node2 当前事实

公网入口已经部署并实测：

- iKuai 将 `222.195.89.236:13300` 转发到 `192.168.2.5:13300`。
- Rootless Docker 的 Nginx 监听 `0.0.0.0:13300`；其余业务服务只在 Compose 网络内通信。
- 服务器端和外部客户端对 `/`、`/health`、`/ready` 的最近验收均为 HTTP 200。
- 证书为自签名证书，SAN 包含 `localhost`、`127.0.0.1`、`192.168.2.5` 和 `222.195.89.236`；浏览器会显示信任警告。
- 当前映射没有源 IP 白名单，因此这是公网入口，不应再描述为“仅校园网访问”。登录限速和应用鉴权不构成网络边界。
- 历史健康检查只证明检查当时的状态；任何后续部署前都必须重新检查容器、迁移、端口、卷和备份。

可用回滚基线：

- 实验室高位端口切换前：`/data/shr/ahamark-backups/lab-port-20260817T115028Z`
- 公网 Host 切换前：`/data/shr/ahamark-backups/public-host-before-20260817T123633Z`
- 账号创建后的 PostgreSQL 备份：`/data/shr/ahamark-backups/post-account-20260815T075913Z-5eda608`
- 本次升级前完整备份：`/data/shr/ahamark-backups/pre-upgrade-056f039-20260818T050009Z`；PostgreSQL custom dump、MinIO 归档、配置、证书和 SHA-256 清单均已验证
- `0049` downgrade 前 PostgreSQL 备份：`/data/shr/ahamark-backups/pre-downgrade-0049-20260818T064159Z`；custom dump 为 583866 bytes，`pg_restore --list` 1232 行，SHA-256 校验通过
- `789a59d` 三次进入完整备份阶段的备份：`/data/shr/ahamark-backups/pre-upgrade-789a59d-20260818T095021Z`、`pre-upgrade-789a59d-20260818T095958Z`、`pre-upgrade-789a59d-20260818T100725Z`；每次 PostgreSQL custom dump、`pg_restore --list`、MinIO 归档和 `SHA256SUMS` 均通过，最终一次 PostgreSQL dump 为 583538 bytes、列表 1231 行

### 安全边界

- 不把私有图片、正文、姓名、学号、原文件名、来源映射、密码、令牌或连接串写入 Git、数据库迁移、公开日志或聊天。
- 不把 OCR confidence、合成评测或 Fake Provider 指标称为真实准确率。
- AI/Codex 只能生成 suggestion；不得自动确认答案、评分标准、最终成绩或成绩发布。
- RapidOCR 默认 runtime 与所有运行时下载继续 hard-off；只有固定清单、显式启用的 node2 专用镜像可运行本地印刷体 OCR。公式区域自动检测默认关闭。
- 不暴露 PostgreSQL、Redis、MinIO、内部 API、Web 开发端口或 Docker socket。
- 不使用 `git reset --hard`、`git checkout --`、强制推送、`docker compose down -v` 或 `docker system prune`。
- 不处理未知卷、非空 Bucket、其他用户容器或 node2 上既有的 80/81/443/8080/8081 服务。
- SSH 密码和验证码只能由用户在可见终端输入；不得读取、记录或回显。

### 已知未完成项

1. 用户名登录和 `789a59d` RapidOCR 固定 bundle 尚未同步 node2；代码、提交哈希镜像、上传和独立镜像诊断均已完成，但部署自动化最终仍在 `image_validation` 阶段中止。服务器已完整回滚到 `5eda608 + 0048`，未创建用户名账号；再次部署前必须先把部署脚本改为可审计的服务器文件工件并逐项保留失败命令/返回码，不直接重复本轮命令。
2. 公网入口仍使用自签名证书；手机 Safari 登录和完整教师流程尚未验收。
3. 公网端口无来源限制；若后续恢复“仅校园网”目标，需要由 iKuai/防火墙实施边界并做内外双向实测。
4. 私有 OCR/Gold 的两页修复输出位于仓库外，未合并或覆盖原 60 页草稿；保持暂停。
5. 真实 OCR、手写、公式、复杂版面和真实 Provider 质量没有生产证据。
6. 全离线公式 OCR 与本地 Qwen 候选服务已完成本地链路验证，代码已以 `de22415` 提交推送且三个提交哈希镜像已构建，但 node2 容量尚未只读确认，模型和镜像尚未上传，线上仍为 unavailable。部署前必须取得内存、CPU、磁盘清单并验证至少 8 GiB 可用内存和足够 Docker/模型空间；不满足则保持 unavailable，不以 swap 强行上线。

### 下一步顺序

下一步仅用一次可见 SSH 会话只读取得 node2 CPU/内存/磁盘，并在容量满足时上传已验签模型和带提交哈希的 API/Web/Formula/llama.cpp 镜像归档。不得直接重复旧 SSH 管道脚本。部署流程必须使用带固定 SHA-256、LF 换行、逐命令返回码和持久化非秘密阶段日志的服务器文件工件，在不停止服务的前提下完成全部 image-validation dry-run；只有 dry-run 全绿后，才重新增量备份当前 `5eda608 + 0048`，执行 `0048 -> 0049`、滚动切换 API-A/B、Worker、Web、Nginx 与内部 local-ai 服务，并验证公网 `/`、`/health`、`/ready`、用户名登录界面、无网络合成文字/公式 OCR 和本地 JSON Schema 建议。任一硬门禁失败即恢复旧配置、`5eda608` 与 0048 备份；Provider 质量没有真实证据时仍只能作为需教师确认的建议。

## 产品能力与边界

AhaMark 已实现教师侧的作业创建、资料整理、题目与区域确认、学生答卷上传、批改建议、教师复核、最终成绩快照、成绩发布记录、报表和学情分析。

产品始终区分三层数据：

1. **候选或建议**：OCR、规则或 AI 产出的草稿，可失败、过期或被替换。
2. **教师确认内容**：教师明确确认后的题目、答案、Structured Rubric 和评分决定。
3. **正式结果**：满足版本与完整性门禁后生成的 `SubmissionScoreSnapshot` 和 `GradeRelease`。

任何候选都不能绕过教师确认直接进入正式结果。没有完整快照表示“未完成”，不得按零分处理。

## 系统结构

| 组件       | 技术与职责                                                             |
| ---------- | ---------------------------------------------------------------------- |
| Web        | Next.js 15、React 19、TypeScript、Tailwind CSS 4；教师工作台与分析页面 |
| API        | FastAPI、SQLAlchemy 2、Alembic；认证、权限、工作流与正式数据契约       |
| Worker     | Celery；文件处理、识别、生成、报表等异步任务                           |
| PostgreSQL | 生产关系数据库和审计事实来源                                           |
| Redis      | Celery broker/result backend 与 production 登录共享限速                |
| MinIO      | 原文件、衍生图片与报表对象存储                                         |
| Nginx      | node2 唯一对外 HTTPS 入口                                              |

```text
浏览器 → Nginx → Web / API → PostgreSQL
                         ├→ Redis → Worker
                         └→ MinIO
```

## 快速开始

### Docker Compose（推荐）

```powershell
Copy-Item .env.example .env
# 编辑 .env，替换开发环境专用凭据
docker compose up --build -d
docker compose ps
Invoke-WebRequest -UseBasicParsing http://localhost:8000/health
Invoke-WebRequest -UseBasicParsing http://localhost:8000/ready
```

默认开发入口：Web <http://localhost:3000>，API <http://localhost:8000>。

不要提交 `.env`、数据库文件、`node_modules`、`.next`、运行密钥或对象存储数据。

### 本机进程

要求 Python 3.11+ 和 Node.js/npm：

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm.cmd install
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir apps/api --reload
```

另开终端启动 Worker 和 Web：

```powershell
.\.venv\Scripts\python.exe -m celery -A workers.celery_app:celery_app worker --loglevel=INFO
npm.cmd run dev
```

## 账号与登录

正式认证使用数据库会话和 HttpOnly Cookie。密码使用带随机盐的 scrypt；写请求同时需要 SameSite=Lax CSRF Cookie 与 `X-CSRF-Token`。production 默认会话 12 小时、Cookie 为 Secure，Redis 登录限速默认 300 秒内 5 次失败，Redis 不可用时 fail closed。

当前代码创建账号：

```powershell
python -m app.cli.create_teacher --username teacher01 --display-name "教师"
python -m app.cli.create_student --username student01 --display-name "学生"
```

命令会在终端中两次询问密码且不回显。仓库不提供公共注册。`DEMO_ACTOR_ENABLED` 只允许非 production 开发环境使用。

注意：node2 尚未部署本次用户名版本，线上界面仍保持旧镜像行为；不要在迁移和切换完成前按上述新契约操作线上实例。

## 教师主流程

1. 创建作业，填写名称、截止时间和发布班级。
2. 一次选择或拖入多个 PDF/PNG/JPG；前端先做格式、空文件和单文件大小校验，再按顺序上传。
3. 系统整理试卷并生成题目、答案和 Structured Rubric 候选。
4. 教师逐题核对；必要时旋转页面、手动框选或追加跨页区域。
5. 发布检查只接受当前 active paper、完整分值、确认答案和 active Structured Rubric Set。
6. 已发布作业可创建批改批次并上传学生答卷。
7. 系统匹配学生、处理页面并生成识别与评分建议；歧义、低置信、stale 或 Provider unavailable 均进入人工复核。
8. 教师接受或修改建议，最终生成版本化成绩快照，再明确发布成绩记录。

手动切题默认关闭，只有教师点击“开始手动切题”后才接受一次拖框；切页、旋转或退出会丢弃未保存框。自动切题和重跑保留历史区域，但只有当前 confirmed region 可进入后续证据链。

## OCR、公式与 AI 边界

`RECOGNITION_PROVIDER` 支持以下状态：

- `unavailable`（默认）：禁用识别，但文件转换和预处理仍可用。
- `tesseract`：首选开源印刷体 OCR 基线；默认关闭，必须显式提供固定版本、路径、哈希和 NOTICE。只输出普通文字、坐标和置信度，不生成 LaTeX。
- `rapidocr`：实验对照，产品 runtime 与模型下载继续 hard-off；不得因安装依赖而自动启用。
- `fake`：只允许非 production 自动化测试；production 会安全降级为 unavailable。

公式 Provider 默认 unavailable，公式区域自动检测默认关闭。当前合成与离线评测只验证合同、拒绝边界和可重复性，不代表真实试卷、手写、公式、表格或复杂版面准确率。

私有 Gold 工具完全离线，输出必须人工复核。任何 Codex 草稿中的助手元话术、猜写、Markdown 或正文 LaTeX 都会 fail closed；轻量正文校对集不能称为结构化 Gold 或生产验证。

## 成绩发布与分析

唯一正式成绩来源是 `FinalScoreService` 读取的最新完整 `SubmissionScoreSnapshot`。系统不会回退到 AI 建议、临时 `TeacherReview`、不完整或已 superseded 数据。

`GradeRelease` 固定具体快照并按作业/班级递增版本；released 只表示教师确认了发布数据，不表示学生已经收到。报表由 Worker 生成真实 `.xlsx` 或 PDF，学号按文本处理，外部文本防公式注入，缺失成绩不写零。

AnalyticsSnapshot 固定 GradeRelease，提供分数段、题目、知识点、错误类型和趋势下钻。主观题展示得分率，不使用“正确率”；教学建议明确标记为规则型建议，不冒充 AI 教学助手。

## 测试与质量门禁

### 后端

```powershell
python -m ruff format --check apps/api workers tests
python -m ruff check apps/api workers tests
python -m mypy
python -m pytest -q
python -m alembic heads
python -m alembic upgrade head --sql
```

### 前端

```powershell
npm.cmd run format
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
```

### 每次提交前

```powershell
git diff --check
git status --short
```

同时必须：

- 确认 `ahamark.db` 未变化或不存在。
- 对新增 diff 做秘密、凭据、私有资料、身份字段、图片/正文和二进制聚合扫描。
- 涉及迁移时确认 Alembic 只有一个 head，并在专用测试库验证升级；任何 downgrade 前先确认绝非生产库。
- 涉及前端时运行 Prettier、ESLint、TypeScript、Vitest 和 production build。
- 只报告本轮实际运行的结果，不沿用历史测试数字冒充当前验证。

## 部署与运维

node2 使用 `shr` 用户的 Rootless Docker。专用文件：

- `docker-compose.preproduction.yml`
- `docker-compose.node2.yml`
- `deploy/nginx/node2.conf`
- `deploy/node2/prepare-runtime.sh`

部署前必须备份 Compose、Nginx、runtime、证书和 PostgreSQL，并验证备份可读；固定镜像标签和 SHA-256。只重建明确服务，不操作未知容器、卷、Bucket 或其他宿主端口。

公网地址：<https://222.195.89.236:13300>。当前为自签名证书且无来源白名单。不要把通用 `docker-compose.yml` 或 `docker-compose.proxy.yml` 直接用于 node2：它们会发布开发端口，不符合 node2 的单入口边界。

完整流程见：[运维手册](docs/OPERATIONS.md)、[备份恢复](docs/BACKUP-RESTORE.md)、[故障恢复](docs/FAILURE-RECOVERY.md)和[预生产就绪](docs/PREPRODUCTION-READINESS.md)。

## 文档索引

| 主题           | 文档                                                                                           |
| -------------- | ---------------------------------------------------------------------------------------------- |
| 项目基线       | [PROJECT-BASELINE.md](docs/PROJECT-BASELINE.md)                                                |
| 能力证据       | [CAPABILITY-EVIDENCE-MATRIX.md](docs/CAPABILITY-EVIDENCE-MATRIX.md)                            |
| 数据安全边界   | [DATA-SECURITY-BOUNDARIES.md](docs/DATA-SECURITY-BOUNDARIES.md)                                |
| 产品口径       | [PRODUCT-CAPABILITY-STATEMENTS.md](docs/PRODUCT-CAPABILITY-STATEMENTS.md)                      |
| 权限矩阵       | [AUTHORIZATION-MATRIX.md](docs/AUTHORIZATION-MATRIX.md)                                        |
| 文件安全       | [FILE-SECURITY.md](docs/FILE-SECURITY.md)                                                      |
| 作业生成       | [ASSIGNMENT-GENERATION-ORCHESTRATION.md](docs/ASSIGNMENT-GENERATION-ORCHESTRATION.md)          |
| 中央审查与发布 | [ASSIGNMENT-CENTRAL-REVIEW-PUBLISH.md](docs/ASSIGNMENT-CENTRAL-REVIEW-PUBLISH.md)              |
| 业务 E2E       | [BUSINESS-E2E.md](docs/BUSINESS-E2E.md)                                                        |
| 成绩正确性     | [SCORE-CORRECTNESS.md](docs/SCORE-CORRECTNESS.md)                                              |
| 性能与容量     | [PERFORMANCE.md](docs/PERFORMANCE.md)、[PERFORMANCE-CAPACITY.md](docs/PERFORMANCE-CAPACITY.md) |
| 最终验收       | [FINAL-ACCEPTANCE.md](docs/FINAL-ACCEPTANCE.md)                                                |
| 历史交接       | [HANDOFF.md](docs/HANDOFF.md) 与 Git 历史                                                      |

## 精简变更账本

这里只保留仍影响当前接手决策的记录；实现细节和历史测试证据使用 `git log --oneline`、对应提交 diff 与 `docs/` 验收材料追溯。

| 日期       | 提交/状态        | 结论                                                                        |
| ---------- | ---------------- | --------------------------------------------------------------------------- |
| 2026-08-18 | `789a59d` 未部署 | 固定 RapidOCR bundle 已推送；部署脚本在 image validation 中止并回滚到旧版   |
| 2026-08-18 | node2 完整回滚   | `056f039` 因文字 OCR unavailable 回滚；应用/schema 恢复 `5eda608 + 0048`    |
| 2026-08-17 | `9b129bc`        | 管理员发布用户名账号；已推送，未部署，node2 仍为旧邮箱登录版本              |
| 2026-08-17 | `0da6dd9`        | node2 允许公网 IP Host 与 origin；已部署                                    |
| 2026-08-17 | `cfe2752`        | Rootless Docker 将唯一 Nginx 入口改为宿主 13300；已部署                     |
| 2026-08-17 | `f050618`        | 拒绝 Codex 助手元话术草稿；未部署                                           |
| 2026-08-17 | `4a2cf2a`        | 私有识别快速正文核对模式；未部署，私有工作已暂停                            |
| 2026-08-15 | `5eda608`        | node2 当前应用镜像基线；schema 为 0048                                      |
| 2026-08-07 | `5ae3a78` 及后续 | 选择性实现手动切题和当前 Structured-only 教师流程；没有整分支合并合作者代码 |

GitHub 合作者审计最近结论：Draft PR #1 的 head 已是目标分支祖先，不重复合并；`gyh--001` 的旧迁移链、外部 OpenAI 调用、隐私面、依赖漂移和部署改动不能整分支合入。该任务现由另一 Codex 任务负责，后续以其最新独立审查结果为准。

本次操作未创建账号、未处理私有识别材料、未发布作业或成绩；`789a59d` 未上线，node2 应用与数据库均保持原基线。2026-08-18 最后一次外部只读检查确认 `/`、`/health`、`/ready` 均为 HTTP 200，Worker 为 1，文字 OCR available，公式 OCR 与 assignment generation provider unavailable；这只是运行状态，不是识别准确率或自动判卷验收。升级、降级和三次 `789a59d` 完整备份均保留且已验证。
