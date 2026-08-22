# AhaMark

AhaMark 是面向教师的作业整理、主观题批改与成绩分析系统。核心原则是：自动化只生成候选或建议，正式题目、答案、评分标准和成绩均由教师确认。

> 新接手任务先读[“接手必读：当前状态、问题与下一步”](#接手必读当前状态问题与下一步)。它是仓库唯一状态账本；历史细节以 Git 提交和 `docs/` 中的验收材料为准。

## 目录

- [接手必读：当前状态、问题与下一步](#接手必读当前状态问题与下一步)
- [产品能力与边界](#产品能力与边界)
- [系统结构](#系统结构)
- [仓库导航](#仓库导航)
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

| 项目           | 当前事实（2026-08-22）                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------ |
| 正确工作区     | `D:\OpenAIData\Workspaces\AhaMark`                                                                           |
| 工作分支       | `codex/grading-confirm-results`；当前提交 `c2c7b12` 已推送 `origin/codex/grading-confirm-results`             |
| 应用基线       | 本地与 node2 API/Worker 为 `c2c7b12`，Web 为 `c2c7b12`；Formula/Qwen 为 `0594d10`                         |
| 远端状态       | node2 已完成 `c2c7b12` 受保护部署并通过独立公网复验                                                         |
| 数据库迁移     | 本地与 node2 均为 Alembic 单 head：`0053_disable_forced_password_change`                                     |
| 最新开发       | 整理试卷 UI 与 Provider 失败短路修复已上线；AI 仍只生成待教师确认的建议                                      |
| 本机业务验收   | 隔离合成环境 A–F 已通过并停在成绩发布前；公式不可读/重框专项通过；GradeRelease 为 0                          |
| node2 在线版本 | API/Worker/Web `c2c7b12`、Formula/Qwen `0594d10`、schema `0053`；文字、公式 OCR 与本地建议 Provider available |
| node2 入口     | `https://222.195.89.236:13300`；自签名证书；公网可达，无来源白名单                                           |
| 部署范围       | 只发布 Nginx `0.0.0.0:13300 -> 8443`；数据库、Redis、MinIO、API、Web、Worker、Docker socket 均无宿主发布端口 |
| 私有识别工作   | 暂停；不得继续处理、上传或提交私有 OCR/Gold、图片、正文或来源映射，除非用户再次明确授权                      |
| 主线合并       | `7e88fae` 仅汇合历史，相对已验证代码树无文件差异；未强推，远端主线与功能分支一致                             |

### 当前开发事实

2026-08-22 用户授权完成 `c2c7b12` node2 受保护部署。部署前只读门禁确认活动整理任务为 0、迁移为 `0053_disable_forced_password_change (head)`、原线上 API/Worker `12fbad6`、Web `12fbad6`，公网 `health=200`、`ready=200`、`login=200`。本地构建并校验 API `ahamark/api:c2c7b12`（镜像 ID `sha256:ebc159bb1d37c8d58df6932dbe60db1763663d14ab6e06dd96a7ae1623bdccd2`）与 Web `ahamark/web:c2c7b12`（镜像 ID `sha256:47f295a894597f5bc6f8f58148e0c519eb8e3b4c06832b4e9c33b04b3ec83701`）；Web 显式使用空 `NEXT_PUBLIC_API_URL`，镜像内不含 `http://localhost:8000`，API 镜像 head 为 `0053`。候选归档及 Compose/Nginx/脚本逐文件 SHA-256 校验通过；部署创建并验证完整备份 `/data/shr/ahamark-backups/pre-upgrade-c2c7b12-20260822T081913Z`，轮换本地 LLM 内网密钥，未执行数据库迁移、降级或真实账号/作业/成绩写入。滚动更新 API-A/B、普通 Worker、专用 generation-worker、Web 和 Nginx；Formula/Qwen、PostgreSQL、Redis、MinIO 保持原版本。脚本报告 `DEPLOY_OK`、`migration=none`；独立公网复验 `health=200`、`ready=200`、`login=200`、`/api/admin/accounts/security=401`，十一个容器运行且 generation-worker 为 `ahamark/api:c2c7b12`。失败回滚路径为备份 runtime/Compose/Nginx 与 API/Web/Worker `12fbad6`，数据库保持 `0053` 不变。下一步强制刷新浏览器后，用合成或用户明确授权的测试试卷验证整理试卷全流程；不得把本地模型 available 当作真实准确率证据。

2026-08-21 “AhaMark 助手17”完成 node2 整理试卷长耗时只读诊断和本地正式修复开发，尚未部署。线上任务停在 `extracting_questions | 55` 的直接原因不是前端或数据库：本地 Qwen 日志显示生成仅约 `0.32 token/s`，第一次请求在代码既有 900 秒上限处生成约 277 token 后被取消并立即进入隐藏重试；容器一度约 `16095% CPU`、393 个 PID/线程，宿主 192 个逻辑 CPU 的 load average 达 228，且同时存在其他用户高 CPU 计算，确认是 llama.cpp 默认过量线程、共享宿主争用、Celery 默认按 192 核扩张和 900 秒多次重试叠加。数据库时间为 UTC，首次诊断时任务实际约运行 17 分钟，不是 8 小时。现有“停止整理”仅写取消请求、不能立即断开模型调用，也纳入修复范围。诊断输出曾显示本地 LLM 内网密钥，账本不记录该值；下次受保护部署前必须轮换。

本地候选修复将 node2 Qwen 默认限制为 32 推理线程、32 batch 线程和 32 CPU，通用 Worker 并发限制为 4，并新增只消费 `assignment_generation` 队列、并发 1 的独立生成 Worker；Compose 解析后已确认任务路由与消费者队列一致。本地 Provider 单次上限改为 600 秒、隐藏重试改为 0，题目抽取和单题答案/Rubric 输出分别限制为 1200/800 token，外部 Provider 既有策略不变。运行中取消现在先持久化 `cancelled`、将未完成阶段标记 discarded，再受控 revoke Celery 子进程；迟到或重新投递任务只会 `discarded_late`，不能回写草稿。前端新增当前阶段耗时、任务尝试次数和超过 10 分钟的有界失败提示，AI 仍只生成 suggestion，教师确认与发布门禁未改变。本轮相关后端联合 `81 passed` 且 `ahamark.db unchanged`，最终定向复跑 `6 passed`；前端全量 `45 files / 262 passed / 4 skipped`，最终相关复跑 `35 passed / 4 skipped`；Ruff、改动面 mypy、Prettier、TypeScript、ESLint、Web production build、node2 合成变量 Compose 解析和 `git diff --check` 通过，build 只有既有 SWC lockfile 联网补丁警告。未登录写入 node2、未停止或重启服务、未修改生产任务/数据库、未部署。下一步先在 node2 低风险窗口用脱敏固定输入比较 16/32/48 线程，轮换 LLM 内网密钥，创建并验证 PostgreSQL、MinIO、runtime、Compose、Nginx 和证书备份；再按 local-llm → generation-worker/worker → API-A/B → Web 滚动更新。本次无迁移，失败回滚到 API/Worker `cb1d0d2`、Web `cb1d0d2-webfix`、Formula/LLM `0594d10` 和 schema `0053`，数据库不降级。任何 node2 写入仍须用户再次明确授权。

2026-08-21 关机后继续恢复本地验证环境：Docker Desktop 后端首次未启动，确认 `docker-desktop` WSL 实例为 stopped 后只重启 Docker Desktop 应用进程，没有执行 WSL 全局关闭、卷/网络清理或 Compose down；随后复用既有 `ahamark-local-current` 容器和数据卷恢复七项服务，API、Worker、Web、PostgreSQL、Redis、MinIO、Qwen 均 healthy，数据库为 `0053_disable_forced_password_change (head)`，`/health`、`/ready`、`/login` 均 HTTP 200，生成 Provider 为 `local_openai_compatible/available` 且 suggestion-only。以改动树指纹 `12fbad6` 本地构建候选 API/Worker `ahamark/api:12fbad6`（镜像 ID `sha256:f026786d5989ffdb74e942b58d24c0386d52f0dc676d2e1d90a7f917e4d05d8b`）和 Web `ahamark/web:12fbad6`（镜像 ID `sha256:b84e4b8a8e135378c5a555de74ec52988b46368f34263eb8a2cbd6322d716660`）；API 构建通过固定 RapidOCR 清单校验并确认 Alembic head 为 `0053`，Web 显式使用空 `NEXT_PUBLIC_API_URL` 且镜像内不含 `http://localhost:8000`，26 页 production build 完成。使用脱敏合成变量按正式 `docker-compose.preproduction.yml + docker-compose.node2.yml` 组合执行 `config --quiet` 通过；本地开发 Compose 不能作为 node2 合并基底。候选尚未导出、上传或部署，未连接 node2、未修改生产任务/数据库、未轮换密钥、未创建远端备份；下一步仍须用户明确授权 node2 写入后，才能执行脱敏 16/32/48 线程基准、密钥轮换、完整备份和受保护滚动部署。

2026-08-22 正式修复本地“AI 生成答案与评分标准反复失败”：只读诊断确认 `local-llm` 容器在任务开始前已以退出码 255 停止，且 Compose 原先将其放在 `local-ai` profile、无自动重启；因此 Worker 对每道题收到 `PROVIDER_NETWORK_ERROR`，最新任务题目抽取已完成但 Rubric 阶段只能进入 `partial`。本地 Compose 现默认纳入 `local-llm` 并设置 `restart: unless-stopped`；能力接口改为复用实时 `/health` 探测，Provider 已配置但模型未运行时直接显示 unavailable；生成 Worker 遇到 Provider 不可达/超时/服务器错误时立即停止剩余题目，避免重复等待。新增 Provider 失败短路回归测试；Ruff 通过，生成与 readiness 定向后端测试 `40 passed`、专项回归 `7 passed`、Worker Provider E2E `3 passed`，均保持 `ahamark.db unchanged`。已重建并重启本地 API、Worker、local-llm；七项服务 healthy，`/ready` 与能力接口均显示 assignment-generation Provider available，未修改业务数据，未部署 node2；下一步从浏览器重新整理一次测试作业。

2026-08-21 用户授权 node2 部署后完成线上只读预检：原整理任务 `9cc35091-63f5-4513-8386-5549225a7410` 已为 `failed | extracting_questions | 55`，活动生成任务数为 0；node2 当前 API/Worker `cb1d0d2`、Web `cb1d0d2-webfix`、Formula/Qwen `0594d10`、schema `0053`，十项服务运行且公网 `health=200`、`ready=200`、`login=200`、管理员 API 未登录 `401`。当时 load average 约 36，Qwen 仍无 CPU cgroup 限制（`NanoCpus=0`），确认具备低风险窗口。使用临时 Qwen 容器和固定合成提示完成线程基准：16/32/48 线程分别为 `0.62/0.51/0.48s`（32 与 48 仅差约 0.03 秒）；综合宿主争用和可控资源边界，候选继续采用 32 线程、32 batch、32 CPU。尝试通过 Windows PowerShell 自动 SCP 时发现终端中文编码异常和原生 SSH/SCP stderr 被严格错误策略提前终止；传输已暂停，候选镜像归档尚未确认上传，未创建生产备份、未轮换密钥、未加载候选镜像、未重启或切换任何 node2 服务。已准备纯 ASCII 原生 `cmd.exe` SCP 入口，但尚未启动；下一步从 `scp -P 1022` 继续上传，上传、备份、密钥轮换和滚动部署前保持当前线上版本不变。

随后发现 node2 为 rootless Docker，内核不支持 Docker CFS `--cpus`/`NanoCPUs` 限制；候选已将 node2 `local-llm` 的 `cpus` 字段改为 Compose `!reset null`，保留 llama.cpp `32/32` 线程边界，避免部署时容器启动失败。正式 `docker-compose.preproduction.yml + docker-compose.node2.yml config --quiet` 通过；线上仍未加载候选、未重启服务、未创建备份或轮换密钥。已上传的临时 Compose 文件需在继续部署前用修正版重新校验覆盖。

第一次执行受保护部署脚本在创建 PostgreSQL custom dump 后，因 node2 宿主没有 `pg_restore` 命令而停在备份列表验证；失败发生在 MinIO 备份、镜像加载、runtime 更新、密钥轮换和服务切换之前。脚本已输出 `ROLLBACK_ATTEMPTED_DATABASE_UNCHANGED` 并恢复原配置，线上仍为 API/Worker `cb1d0d2`、Web `cb1d0d2-webfix`、Formula/Qwen `0594d10`、schema `0053`。未删除本次未完成备份目录；修正版改为通过现有 PostgreSQL 容器执行 `pg_restore --list`，不在宿主安装软件。

第二次执行完成完整备份 `/data/shr/ahamark-backups/pre-upgrade-12fbad6-20260821T144545Z`、候选镜像加载、密钥轮换和全部服务滚动切换，但 Web 刚启动后立即单次检查 `/login` 得到 502，触发自动回滚。只读复验确认数据库仍为 `0053`、API/Worker/Web 已回到 `cb1d0d2`/`cb1d0d2-webfix`，但 Nginx 未重建而缓存旧 Web 容器地址，且候选 generation-worker 残留运行；已停止残留 Worker并重启 Nginx，最终 `health=200`、`ready=200`、`login=200`、管理员 API `401`。修正版部署脚本现在先等待 Web HTTP 可达，再强制重建 Nginx，并对公网门禁做有界重试；回滚路径同步停止专用 Worker并强制重建 Nginx。线上当前仍为旧基线，数据库和用户数据未改变。

第三次受保护部署按修正版脚本成功完成，报告 `DEPLOY_OK`、`migration=none`、`backup_verified=true`，完整备份为 `/data/shr/ahamark-backups/pre-upgrade-12fbad6-20260821T145745Z`；数据库未降级，当前 head 仍为 `0053_disable_forced_password_change`。node2 当前 API/Worker 和专用 generation-worker 使用 `ahamark/api:12fbad6`，Web 使用 `ahamark/web:12fbad6`，Formula/Qwen 仍为 `0594d10`，rootless Docker 的 Qwen 不设置 CFS `cpus` 而使用 32/32 线程限制。脚本内 API-A/B、Worker、Web、Nginx 和 Qwen 门禁通过；随后本机独立公网复验 `health=200`、`ready=true`、`login=200`、`/api/admin/accounts/security=401`，ready 显示 Celery workers=2、本地 assignment-generation Provider available 且 suggestion-only，登录页不含 `localhost:8000`。当前线上已部署本次整理试卷长耗时修复；没有创建管理员账号、没有处理或发布真实作业/成绩。下一步先强制刷新浏览器，再用合成或用户明确授权的测试试卷验证整理试卷全流程；不得把本地模型 available 当作真实准确率证据。

2026-08-20 修复 node2 管理员账号页面数据接口 404：根因是管理员网页与 API 同时使用 `/admin/accounts` 命名空间，而 node2 Nginx 只将 `/api`、`/auth`、`/health`、`/ready` 和 `/files` 转发给 FastAPI；因此账号列表请求收到 Next.js 页面 HTML，安全概览和审计请求收到 Next.js 404。只读公网核对证实 `/admin/accounts=200 text/html (Next.js)`、`/admin/accounts/security=404 text/html (Next.js)`、旧 `/api/admin/accounts/security=404 application/json (FastAPI)`，而 `/auth/me=401 application/json`，排除数据库、账号为空或 API upstream 故障。现将前端所有管理员数据、写操作和 CSV 导出统一改为 `/api/admin/accounts/...`，后端在 `/api` 下增加同一受管理员会话、CSRF 和审计保护的兼容路由，同时暂时保留旧直连路由供本地客户端兼容；`/admin/accounts` 网页地址不变，Nginx 无需扩大 `/admin` 转发范围。新增前端 URL 契约和后端生产代理命名空间回归。验证结果：管理员后端 `12 passed` 且数据库守卫为 `ahamark.db unchanged`；前端 API/管理员页 `10 passed`；Ruff、Prettier、TypeScript、ESLint 和 Web production build 通过。为提高关机后首次构建在慢速网络下的稳定性，标准 API 与 RapidOCR Dockerfile 的 pip 安装读取超时统一提高到 300 秒；重试后本地 API 镜像构建成功，API/Web 重建后六项默认 Compose 服务 healthy，`/login`、`/health` 均 HTTP 200，迁移为 `0053_disable_forced_password_change (head)`，`/api/admin/accounts/security` 未登录返回 401（API 路由已命中），网页路径 `/admin/accounts/security` 仍由 Next.js 返回 404（这是预期的页面与 API 分离）。本地默认 Compose 的作业生成、OCR 和评分 Provider 按配置 unavailable；node2 只做了上述公网只读请求，尚未上传、部署、重启、迁移或修改账号/数据库。下一步按既有受保护流程备份 PostgreSQL、MinIO、runtime、Compose、Nginx 和证书，滚动更新 API-A/B 与 Web；回滚到当前 `e375e55` API/Worker、`c444019` Web 和 schema `0053`，本次不需要数据库迁移。

2026-08-21 已按用户授权完成 node2 管理员接口修复部署。候选工作树指纹为 `cb1d0d2`；本地构建并以固定 RapidOCR 清单校验 API `ahamark/api:cb1d0d2`（镜像 ID `sha256:a9c5f0951f1c54f07c0b4de1acbd68fabac6a334e9aaf8875fe0c1f2cb0e483f`）与 Web `ahamark/web:cb1d0d2`（镜像 ID `sha256:c061d20da6484371875e19bfe535dd1637b7a249bbf2b3c5861aa550e691fc97`），部署归档 SHA-256 为 `6d4e7c81f317dc798afb38da907c089926f9e6a2cfb1d91b7e32b6b9b912d7cc`。服务器先完成并逐文件校验完整备份 `/data/shr/ahamark-backups/pre-upgrade-cb1d0d2-20260820T164314Z`（PostgreSQL custom dump/list、MinIO、runtime、Compose、Nginx 配置和证书），再校验当前 API/Worker `e375e55`、Web `c444019` 与 schema `0053`，加载候选镜像并按 API-A → API-B → Worker → Web 滚动切换；本次明确未执行数据库迁移、未重启模型/数据库/缓存/对象存储。部署脚本最终报告 `DEPLOY_OK`、`head=0053_disable_forced_password_change`、`forced_flags=0`、`migration=none`；十项 Compose 服务保持运行，API-A/B healthy，Worker/Web running，脚本内 `/health`、`/login`、`/ready` 和 `/api/admin/accounts/security=401` 检查通过。随后从 node2 公网入口本机独立复验：`health=200`、`ready=200`、`login=200`、`admin_api=401`，确认 Nginx → FastAPI 管理员 API 路由已生效。失败回滚仅恢复本次备份的 runtime 并拉起旧应用镜像，数据库保持不变。下一步再处理管理员真实账号创建；不要恢复旧的 `/admin/accounts` API 调用，也不要执行数据库降级。

2026-08-21 修复公网浏览器显示“无法连接服务器”：node2 后端入口只读检查一直正常，但先前 Web 镜像沿用本地默认构建参数 `NEXT_PUBLIC_API_URL=http://localhost:8000`，导致公网浏览器把 API 请求发往用户自己的电脑。现用生产构建参数 `NEXT_PUBLIC_API_URL=` 重建 Web `ahamark/web:cb1d0d2-webfix`（镜像 ID `sha256:881d0dbe9bb0d77c0050ef85025be249e2370c6ea2538d03cd2bad9f42786061`），并在镜像内确认不再包含该 localhost 地址。服务器先创建并校验备份 `/data/shr/ahamark-backups/pre-upgrade-cb1d0d2-webfix-20260820T170220Z`，仅滚动重建 Web，API `cb1d0d2`、数据库 `0053`、Worker、Nginx、模型和数据服务均未改变；脚本报告 `WEBFIX_OK`、`migration=none`。修复后公网独立复验 `/health=200`、`/ready=200`、`/login=200`、`/api/admin/accounts/security=401`，浏览器需使用强制刷新清除旧 JS 缓存。后续生产 Web 构建必须显式传入空的 `NEXT_PUBLIC_API_URL`，不得使用本地默认值。

2026-08-20 记录 node2 登录入口纠错：服务器公网 SSH 正确端口为 `1022`，不是默认 `22`。此前使用 `ssh shr@222.195.89.236` 虽能连到 22 端口，但在键盘交互认证后被服务器关闭；改用 `scp -P 1022` 已成功完成文件传输，确认 `shr` 用户、密码和验证码流程可用。以后所有 node2 SSH/SCP 操作必须显式使用 `-p 1022`/`-P 1022`，不得省略端口或回退到 22；密码和验证码仍只能由用户在可见终端输入，不写入命令、日志或账本。本次只读健康检查仍为 `/health=200`、`/ready=200`；管理员账号创建尚未执行。

2026-08-20 “AhaMark 助手16”完成本地接手核对和一轮教师流程可理解性修复：当前分支仍为 `codex/grading-confirm-results`，HEAD 为 `dde8b68`，唯一既有未跟踪项仍是用户资料目录“数学分析资料整理”，本轮未读取、修改或删除。教师“整理试卷”原已按必经流程阻止跳到核对/发布，但在“无任务且生成 Provider 不可用”时只把启动按钮置灰；现新增明确的“AI 辅助暂不可用”提示，展示后端安全错误代码，说明整理试卷是必经流程、恢复后应开始或重新整理，且未改变 suggestion-only、教师确认或发布门禁。同步修正两条答案/Rubric 测试夹具，使其在进入中央审查前明确模拟生成任务完成到 `review_required`，避免旧夹具绕过新门禁。2026-08-20 执行 `git fetch --prune origin` 后，协作者 `origin/gyh--001` 仍停在已审查的 `d4ba9ba`，`origin/master` 也没有当前 HEAD 尚未包含的提交；既有结论仍是按功能选择性吸收，不整分支合并、不改写历史迁移。

本轮验证：整理试卷组件 `18 passed`；教师向导、答案/Rubric、中央发布、创建作业、班级学生关联和管理员账号页面联合 `6 files / 92 passed`；TypeScript、ESLint、后端 Ruff 通过；Alembic 为单 head `0053_disable_forced_password_change`。后端七个关键文件联合运行首次为 `116 passed, 2 failed, 1 error`：两条失败均因旧夹具未把任务推进到 `review_required`，修正后定向复跑 `2 passed`；迁移用例错误来自系统临时目录拒绝访问，改用工作区隔离临时目录后 `1 passed`。两次后端测试数据库守卫均报告 `ahamark.db unchanged`。Docker Desktop 引擎当前未运行，`docker compose ps` 无法连接；本地 `localhost:3000/login`、`localhost:8000/health` 和 `/ready` 无服务监听，因此本轮不能把容器健康、实际数据库当前 revision 或登录页运行态记为通过。未登录 node2、未部署、未创建账号、未修改生产数据或发布成绩。下一位接手应先恢复本地 Docker 服务，再只读复验六项 Compose 健康、实际数据库 revision、登录页和一次上传→整理→核对→发布前浏览器闭环；如需 node2 部署，仍须先提交变更摘要、备份与回滚方案并获得明确授权。

2026-08-20 继续完成本地运行态验收：Docker Desktop 以 `desktop-linux` 恢复后，复用既有 `ahamark-local-current` 卷启动 API、Worker、Web、PostgreSQL、Redis、MinIO 和 local-llm；七项服务均为 healthy，容器内 `alembic current` 为 `0053_disable_forced_password_change (head)`，`/login` 与 `/health` 均 HTTP 200，`/ready` 为 `ready=true`、Worker=1、作业生成 Provider 为 `local_openai_compatible/available` 且 suggestion-only。新建默认 Compose 网络因本机地址池已耗尽而失败，未删除任何网络或卷，改为复用已有本地项目。隔离合成业务浏览器验收 `BUSINESS_BROWSER_E2E_STOPPED ... stages=6 completed_through=F` 通过：登录、班级/学生、创建作业、上传试卷、题目/Rubric、教师确认均通过，安全停止于发布前，`grade_release_write_attempted=false`；运行证据已刷新至 `docs/business-e2e-verification.json`。浏览器首次因系统 Temp 权限失败，切换到隔离证据目录后通过；业务 E2E 容器已 stop，卷保留，`ahamark-local-current` 保持运行。未登录 node2、未部署、未发布成绩；本地 readiness 中主观评分、AI grading、文字 OCR 和公式 OCR 仍 unavailable，符合当前本地配置，不能把本次 A–F 合成验收描述为真实识别或评分质量证据。

2026-08-21 应用户要求重启本机 Docker Desktop；Linux 引擎恢复为 Docker `29.6.2`。默认 `ahamark_default` 因历史测试网络占满地址池而无法创建，未删除网络、卷或数据库，改用已有项目 `ahamark-local-current` 启动本地 API、Worker、Web、PostgreSQL、Redis、MinIO。当前 `/health=200`、`/login=200`，Web/API 已监听 `localhost:3000/8000`；Worker 仍在启动健康检查中。node2 未连接、未部署。

2026-08-21 根据本地浏览器截图复核：截图中的“历史记录（0）”来自旧 Web 容器镜像；当前源码已移除该展示。仅重建并重启本地 `ahamark-local-current-web-1`，Web 构建通过，`localhost:3000/login=200`；未修改 API、数据库或 node2。

2026-08-21 按用户要求调整文件用途流程：将“确认文件用途”移到“上传题目与答案”区域；上传成功后自动启动一次整理任务，先由现有文件分析阶段给出用途建议，再在上传区域确认题目/答案/题目和答案，确认后继续生成。整理面板中的原文件用途区已隐藏，避免重复展示；文件用途确认仍由教师明确操作，未改变 suggestion-only 和发布门禁。Web 本地重建通过，`localhost:3000/login=200`；尚未上传或部署 node2。

2026-08-21 修复上传区域确认答案文件时的门禁提示：后端要求答案类文件同时提交 `confirmed_answer_source`，此前新 UI 对答案角色误提交 `not_applicable`，因此显示“答案文件必须由教师确认答案来源”。现已增加“答案来源”下拉框（教师提供、官方、第三方、AI、未知等），题目文件仍自动提交“不适用”；TypeScript 和 Web production build 通过，本地 Web 已重建，未部署 node2。

2026-08-21 根据用户确认，答案文件来源不再让教师选择，固定提交 `teacher_provided`；题目文件继续提交 `not_applicable`。已移除上传区域的答案来源下拉框，TypeScript 和 Web production build 通过，本地 Web 已重建，未部署 node2。

2026-08-21 按用户要求将“已上传到此作业”和“确认文件用途”合并为同一张上传卡片；用途确认显示在已上传文件列表下方，减少重复分区。TypeScript 和 Web production build 通过，本地 Web 已重建，未部署 node2。

2026-08-22 进一步按用户指定的两行布局调整上传卡片：每个文件只显示一条记录，第一行是“文件名 + 删除”，第二行是“文件用途 + 确认状态/确认按钮”；不再重复展示独立上传列表和用途文件卡。TypeScript 和 Web production build 通过，本地 Web 已重建，未部署 node2。

2026-08-20 继续诊断教师题目清单出现 `1–5` 后再次出现 `1–5`：对本机作业 `04fe222d-0983-4f22-8580-0580790ae477` 只读核对确认，当前试卷版本内确有 10 条 active `ai_draft`，分别由 17:19 和 17:26 两次生成任务各物化 5 题，并非前端重复渲染。根因是旧逻辑只在同一草稿修订内淘汰旧候选，而“重新整理”会创建新修订，导致上一轮未审核 AI 草稿仍 active、下一轮继续追加。现已修复为：新一轮存在有效候选时，先将同作业、同试卷版本、历史修订中未被教师审核的 active `ai_draft` 标记 removed，并把对应候选标记 superseded；教师已审核题目保持不变；当前候选若与保留题目或同批候选题号冲突，则标记 `QUESTION_NUMBER_CONFLICT` 和 `manual_required`，不再自动物化同号题；新草稿顺序只基于 active 题目计算。新增回归覆盖“保留已审核题、替换未审核旧草稿、冲突转人工核对且 active 清单题号唯一”。验证结果：`tests/test_assignment_generation.py` 为 `31 passed`，Provider/Worker 与答案/Rubric 相邻链路 `43 passed`，两次数据库守卫均为 `ahamark.db unchanged`；改动文件 Ruff 和两份后端源文件 mypy 通过。本地 API/Worker 已用新代码重建。经用户明确授权后，先创建并校验修复前 PostgreSQL custom dump `tmp/local-backups/assignment-dedupe-20260820T175033/ahamark-before-assignment-dedupe-20260820T175033.dump`（681268 bytes、1239 个 TOC 条目、SHA-256 `6BC01A62FECB3826668846C80ABFF1469224930A02DB88FE5B823B02A15922B7`），再通过 AhaMark 自身任务服务启动第 12 次“重新整理”。任务 `96146ab4-2977-4e36-8c85-62c05cf00c6a` 已完成至 `review_required | 100`，分析、页面检查、题目抽取、答案/Rubric 和结构验证五阶段均 completed；当前只剩 5 条 active `ai_draft`（题号与顺序均为 `1–5`），原 10 条重复草稿保留为 removed 软失效记录，没有物理删除；新任务有 5 个物化候选、5 份答案草稿和 5 份 Rubric 草稿，验证为 `unsupported=5, failed=0`，仍要求教师人工核对。七项本地服务 healthy，`/login`、`/health`、`/ready` 均为 HTTP 200，数据库仍为 `0053_disable_forced_password_change (head)`，作业生成 Provider 为 `local_openai_compatible/available`。下一步由教师核对并接受当前 5 题及答案/Rubric，再进行发布前检查；不要恢复 removed 历史草稿。未登录 node2、未部署 node2、未发布作业或成绩。

2026-08-20 修复教师向导中的重复入口：第 2 步旧的“题目列表”手工编辑器与“整理试卷”面板的题目核对/结构确认职责重叠，且会让教师误以为需要维护两份题目。现移除旧编辑器的渲染、状态和直接创建/更新题目逻辑；经用户确认，保留“题目列表”作为唯一对外名称，由安全的整理试卷面板统一承载 AI 候选核对、教师接受、合并/拆分和结构确认，“继续核对题目”也统一指向该面板。Provider 不可用提示同步改为在恢复前不能进入“题目列表和发布”。本轮回归 `43 passed, 4 skipped`（4 个仅覆盖已移除旧编辑器的历史测试），Web TypeScript、ESLint、Prettier 和 production build 通过；本地 Web/API 重建后七项服务均 healthy，`/login`、`/health`、`/ready` 均 HTTP 200，数据库仍为 `0053_disable_forced_password_change (head)`，作业生成 Provider 为 `local_openai_compatible/available`。未改动题目数据、未部署 node2、未发布作业或成绩。下一步由教师刷新当前编辑页，按唯一“题目列表”继续核对 5 道当前题目及答案/Rubric，再做发布前确认。

2026-08-19 在不登录 node2、不使用真实账号或资料的前提下，已完成本机隔离业务安全验收：当前三步作业向导、低质量图片阻断、教师手动切题、文件用途确认、题目/Rubric、发布检查、两名合成学生提交、处理 v3、教师页面手动画框、自动答案识别、Codex-local 建议和 4/4 教师复核全部通过。主浏览器证据为 `passed_through_F`，脚本固定在 F 后停止，`grade_release_write_attempted=false` 且 GradeRelease 查询计数为 0；G/H 未运行。公式专项另行通过“质量阻断→明确标记不可识别→重新框选恢复人工待确认”。本地 Provider 为 fake/合成适配器，因此这些结果只证明工作流、持久化、来源和门禁，不证明真实 OCR/公式/评分准确率。复现范围和限制见 `docs/BUSINESS-E2E.md`。

2026-08-19 继续完成教师逐题复核体验改进：评分入口按建议状态统一为唯一的“修改分数”或“手动评分”，进入编辑后自动定位并聚焦最终分数；分项缺失、越界或合计不一致会在表单内即时显示并禁用保存，校验通过后明确显示可保存状态；异常卡片不再重复展示同名入口，快捷键 E 统一描述为“打开评分”。TeacherReview 继续保留 `modified`/`manual_scored` 的教师决策语义，GradingResult 按既有正式契约投影为 `modified`。目标页 32 条测试、前端全量 `38 files / 235 tests`、TypeScript、ESLint、业务契约 62 条和重新构建后的本机 A–F 浏览器闭环均通过；仍在 GradeRelease 前停止。

2026-08-19 在用户授权离线自主开发、无需登录 node2 的范围内，新增管理员账号运维闭环：`admin`、`teacher`、`student` 三类账号使用显式且互斥的创建角色，历史无角色教师继续兼容；管理员登录后进入 `/admin/accounts`，可查看分类与在线会话摘要、搜索筛选和分页、创建账号、修改展示姓名、启停账号及重置他人密码。停用和重置密码都会撤销目标账号全部未撤销会话；当前管理员不能停用或在该页面重置自身密码，至少保留一个启用管理员。所有写操作要求管理员会话与 CSRF，创建、更新、重置均写审计日志且不记录或回显密码。没有提供物理删除或角色直接变更，避免误删业务归属或越权迁移。账号/认证后端专项 `12 passed` 且测试数据库守卫确认 `ahamark.db unchanged`；strict mypy `131 source files`、全后端 Ruff、前端 TypeScript/ESLint、全量 `40 files / 242 tests` 和包含 `/admin/accounts` 的 20 页 production build 通过。该功能当前只完成本地代码和隔离测试，尚未部署 node2、尚未创建真实管理员账号。

2026-08-19 用户授权把已完成工作整合到当前 D 盘工作区。`codex/grading-confirm-results` 原 `278d899` 是完整功能分支的严格祖先，已使用 `git merge --ff-only` 无冲突快进至 `9bccb91`；该提交包含管理员账号运维、教师逐题复核体验和本机业务验收脚本改进。当前工作区原有公式评测及临时测试目录均为未跟踪内容，整合过程未修改、删除或纳入提交。D 盘复验前按 `.[dev]` 安装项目声明的开发依赖；账号/认证专项 `12 passed`、测试数据库守卫 `ahamark.db unchanged`、Ruff、strict mypy `131 source files`、前端 `40 files / 242 tests`、TypeScript、ESLint 和包含 `/admin/accounts` 的 20 页 production build 均通过。Next 构建仍出现既有 SWC lockfile 自动补丁网络告警，但编译、静态页面生成和构建进程成功。尚未 push、未登录 node2、未部署、未创建账号或发布成绩。

2026-08-19 继续完成无需登录 node2 的管理员账号运维增强：管理员可下载模板并在浏览器本地预览 CSV，一次批量创建最多 200 个教师或学生账号；中英文表头和账号类型均受支持，无效行会在提交前标出，密码不进入预览、响应或审计。管理页新增最近账号操作，显示执行管理员、目标账号、动作和时间；后端为批量导入写逐账号及汇总审计，仍拒绝 CSV 创建管理员。合成账号浏览器闭环 9 个场景通过；账号/认证后端 `14 passed` 且 `ahamark.db unchanged`，Ruff、strict mypy `132 source files`、前端 `41 files / 246 tests`、TypeScript、ESLint、20 页 production build、Alembic 单 head `0049_usernames` 和业务 Compose 静态解析均通过。Docker Desktop 引擎因本机 Windows 服务权限被拒绝而无法启动，因此没有获得 PostgreSQL 实机迁移、容器重启或 Compose 健康证据；SQLite 也因既有迁移使用 PostgreSQL `JSONB` 而不能替代该证据。操作说明见 `docs/ADMIN-ACCOUNT-OPERATIONS.md`。本轮未 push、未登录 node2、未部署，也未创建真实账号。

2026-08-19 对全仓 595 个已跟踪文件和根目录运行产物完成结构盘点：已跟踪文件没有内容完全重复项，现有 Markdown 相对链接没有断链。为避免破坏 API 导入、脚本默认路径、Compose 和历史证据引用，本轮不做无收益的大规模搬移；改为在 `apps/`、`data/`、`deploy/`、`docs/`、`scripts/`、`tests/`、`workers/` 增加职责与导航 README，并由根 README 提供统一仓库地图。`.gitignore` 和 `.dockerignore` 新增历史 pytest/mypy/ruff、临时验收、公式评测生成目录及本地资料目录规则，Git 状态不再扫描这些不可访问缓存，Docker 构建上下文也不再携带这些本地产物；新增硬化测试保证主要目录导航、Markdown 相对链接和忽略规则持续有效。原有 `.env`、数据库、公式评测结果和“数学分析资料整理”等本地资料均未读取、移动、删除或提交。仓库硬化专项 `9 passed`；仓库硬化、数据库隔离、node2 部署契约和业务浏览器契约联合为 `100 passed, 1 skipped`，两次数据库守卫均为 `ahamark.db unchanged`。Ruff、strict mypy `132 source files`、前端 `41 files / 246 tests`、TypeScript 和 ESLint 通过。全后端收集为 1094 项；一次与前端门禁并发的完整运行早期出现单个未留 traceback 的失败，串行 fail-fast 前段未复现，但因整套包含长耗时容量/恢复测试，本轮没有把中止运行当作全量通过证据。本轮未 push、未部署、未登录 node2。

2026-08-19 完成管理员账号运维第二阶段的本机开发：账号表支持选择当前页的非本人账号并批量启用、停用或强制下线，每次最多 200 个目标；服务端要求显式确认、逐项返回成功与失败，批量停用继续保证至少一个启用管理员，并通过 PostgreSQL 行锁收紧并发停用保护。导出沿用当前搜索/类型/状态筛选，只包含账号状态与登录活动，UTF-8 CSV 对电子表格公式前缀做转义。新增账号安全面板，统计已识别账号 24 小时失败登录、活动会话、多设备、从未登录和 90 天未活动账号，并允许撤销非当前会话；失败登录审计不保存用户名、密码或客户端 IP。开发时同时修复成功登录也消耗失败限流额度的既有缺陷，现只累计失败且成功登录清零。所有批量和会话动作均受管理员会话、CSRF、二次确认语义和审计约束。账号/认证专项 `19 passed` 且 `ahamark.db unchanged`；全后端 Ruff、strict mypy `132 source files`、前端 `41 files / 249 tests`、Prettier、TypeScript、ESLint 和包含 `/admin/accounts` 的 20 页 production build 均通过。Next 构建仍只有既有 SWC lockfile 网络补丁告警，编译和构建成功。本轮未 push、未部署、未登录 node2，也未创建真实账号。

2026-08-19 审查 `origin/gyh--001` 的 4 个独有提交后开始第一批选择性吸收，没有整分支合并，也没有修改历史迁移。浏览器请求失败现在统一为可识别的 `NETWORK_ERROR`，主动取消仍保留 `AbortError`；Office 检查改为解析关系 XML，并拒绝宏、ActiveX、嵌入对象、可执行内容和外部链接，PDF 额外拒绝脚本、自动动作、表单、附件及多媒体。教师设置页已连接真实账户偏好接口，可更新展示姓名并保存带版本号的工作台偏好，默认班级受租户归属校验，学生账号不能使用教师偏好接口，API 密钥和外部 AI 开关不会传给页面。两个无引用的历史演示文件已删除。后端相关 `40 passed`、前端全量 `42 files / 252 tests`、Ruff、strict mypy、TypeScript 和 ESLint 均通过；测试数据库守卫为 `ahamark.db unchanged`。本轮未登录 node2、未部署、未创建真实账号或发布成绩。

2026-08-19 继续完善教师“错题与练习”：`/practice` 不再展示演示数据，改为只读取当前教师名下每个作业/班级的最新 released `GradeRelease` 及其固定正式快照，满分题自动排除；教师可按班级、作业、错误类型和学生/题目/知识点关键词筛选，并查看学生答案、最终反馈、得分率及原批改入口。接口复用正式成绩校验，题目必须属于发布试卷、答案必须属于对应提交与题目、知识点和学生必须属于当前教师；显式学生或管理员账号均不能访问，历史无角色教师继续兼容。页面不调用外部 AI，也不自动创建练习，教师可从真实错题判断后进入现有新建作业流程。相关后端联动 `32 passed`、前端全量 `43 files / 254 tests`、Ruff、strict mypy `133 source files`、Prettier、TypeScript、ESLint 和 production build 均通过；测试数据库守卫为 `ahamark.db unchanged`。全仓 Ruff 格式检查仍报告 21 个既有 CRLF/历史格式文件，本轮未批量改写无关文件。本轮未登录 node2、未部署、未创建真实账号或发布成绩。

2026-08-19 补齐错题到练习草稿的教师操作闭环：教师可跨筛选和分页选择最多 50 条正式失分记录，页面按原题去重并汇总目标班级；点击后复用现有作业接口创建 `draft`，预填原作业、题号和知识点复习清单，再进入既有上传与编辑步骤。草稿载荷不会从错题记录复制学生姓名、学号、学生答案或个别反馈，也不会复制题目、调用 AI 或自动发布；教师仍需自行设计/上传练习题并完成答案、Rubric 和发布确认。专项前端 `3 passed`、前端全量 `43 files / 255 tests`、错题后端契约 `3 passed` 且测试数据库守卫为 `ahamark.db unchanged`；Prettier、TypeScript、ESLint 和 production build 均通过。本轮未登录 node2、未部署、未创建真实账号或发布成绩。

2026-08-19 继续检查练习草稿落地后修复一个教师可见断点：后端原本已保存 `instructions`，但三步作业编辑器没有展示或更新该字段。现在带复习范围的草稿会自动展开“更多设置”，教师可查看、修改并保存“作答要求或复习范围”；作业详情页也会展示作业说明与该范围。错题页生成的首行改为师生均可安全阅读的薄弱点说明，避免未来学生端展示时出现教师内部操作话术。相关专项 `22 passed`、前端全量 `44 files / 257 tests`、Prettier、TypeScript、ESLint 和 20 页 production build 均通过；Next 仍只有既有 SWC lockfile 网络补丁告警。本轮未登录 node2、未部署、未创建真实账号或发布成绩。

2026-08-19 改进学生账号与班级关联体验：教师不再通过学生档案邮箱间接查找登录账号，班级页改为搜索并选择启用的学生账号，确认后直接保存既有 `Student.user_id` 关系；列表同步显示已关联用户名，学生档案邮箱降为可选联系信息。候选接口只允许教师访问，只返回启用、显式 `student` 且尚未关联到该教师其他档案的账号；服务端继续校验账号状态、学生角色、档案归属与同教师唯一关联，已有 `user_id` 数据无需迁移。班级、学生门户与权限矩阵后端联动 `16 passed`，数据库守卫为 `ahamark.db unchanged`；Ruff、strict mypy `133 source files`、前端全量 `44 files / 258 tests`、Prettier、TypeScript、ESLint 和 20 页 production build 均通过，Next 构建仍只有既有 SWC 锁文件联网补丁告警。当前本地 `ahamark-local-current` API/Web 已重建，六项 Compose 服务健康，班级页面 HTTP 200 且 `/ready` 为 `ready=true`；本地非 AI Compose 的 OCR/AI Provider 仍按配置 unavailable，不属于本次改动。本轮未登录 node2、未 push、未部署或发布成绩。

2026-08-19 按用户“全部吸收、自主开发”的授权继续处理 `origin/gyh--001` 剩余产品能力，仍不整分支合并、不改写历史迁移。新增账号自助改密，改密撤销其他会话并写审计；随后按用户决定以 `0053` 清除全部强制改密标记，新建账号和管理员重置密码均不阻断登录，改密入口仅供自愿使用。`0051` 建立学生正式错题复核申请，教师可维持原判、要求补充或带 `ScoreRevision` 改分，既有发布快照不被覆盖；`0052` 为现有班级资料增加显式学生发布/撤回，只有账号已关联且仍在班的学生可见和下载。学生端新增正式错题、复核状态、在线多文件/多版本提交、学习资料和基于最新正式成绩的学习分析；在线提交复用文件安全检查并进入教师批改批次，不依赖邮箱或文件名匹配。评分标准库沿用现有更完整的跨作业版本化模板实现，没有创建第二套重复模型。学习助手默认关闭，仅在显式启用本地 OpenAI-compatible Provider 且外部请求关闭时可用，只解释错因和提供练习建议，不能评分、改分或发布成绩。后端改动面联动为 `40 passed, 1 skipped`，数据库守卫为 `ahamark.db unchanged`；全仓 Ruff、strict mypy `123 source files`、前端首次全量 `45 files / 261 tests`、Prettier、TypeScript、ESLint 和 33 页 production build 通过。第二次前端全量出现一个既有评分页异步展开测试波动，目标文件单独复跑 `32 passed`。本地 PostgreSQL 升级前 custom dump 有 1221 个 TOC 条目，`0049 -> 0053` 实迁成功，现有强制改密标记数为 0；新增列/表核对通过，六项 Compose 服务健康，`/health`、`/ready` 与登录页均为 200。完整后端套件运行到 12% 仍零失败后因既有长耗时容量/识别用例停止，不能记为完整通过。本轮未登录 node2、未 push、未部署或发布成绩。

2026-08-19 用户授权更新 node2。候选 `c444019` 的 API/Web 镜像、`0053` head 和固定 RapidOCR 清单本地校验通过；服务器先完成 PostgreSQL、MinIO、运行配置、Compose、Nginx 与证书备份并验证 SHA-256，备份为 `/data/shr/ahamark-backups/pre-upgrade-c444019-20260819T120754Z`。候选迁移容器随后在读取既有 Compose 传入的无引号方括号 host allowlist 时被 Pydantic Settings 拒绝，失败发生在连接数据库前；运行配置和业务容器尚未切换，公网 `/`、`/health`、`/ready` 复验仍为 200/available。修复镜像 `e375e55` 上传后，第二次迁移仍在连接数据库前被相同错误阻止；比对镜像源码行号确认部署脚本加载旧 `runtime.env` 时把旧镜像标签导出到 Shell，优先级覆盖候选 env-file，实际再次运行的是旧镜像。第三次脚本清除旧镜像变量并核对 Compose 候选镜像后成功完成 `0049 -> 0053` 事务迁移和 API-A/B、Worker、Web 滚动切换；线上 API/Worker 为 `e375e55`，Web 为 `c444019`，既有 Formula/Qwen 镜像保持 `0594d10`，唯一公网端口仍为 13300。迁移后强制改密标记为 0，六项核心服务健康；独立公网复验 `/`、登录、主动改密、学生错题、教师复核、`/health` 均为 200，`/ready` 为 available、Worker 为 1，文字/公式 OCR 与本地建议 Provider available，评分和公式继续保留 suggestion-only/教师确认边界。未创建账号、未处理真实资料、未发布作业或成绩。代码提交尚未 push。

2026-08-19 曾短暂把 Codex 页面整理、题目抽取、答案和评分标准生成设计为可跳过的降级步骤，并完成当时的前端验证；该产品口径已被随后确认的“AI 是必经流程”要求取代，不再代表当前行为，手动跳过入口已删除。

2026-08-19 随后在本机启用真实的离线作业生成辅助：固定 `Qwen3-4B-Q4_K_M.gguf` 已下载到 AhaMark 专用 Docker 卷，文件大小 `2497280256` 字节且 SHA-256 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` 验证通过；llama.cpp 服务仅暴露在 Compose 内网，模型卷只读，外部 Provider 请求保持关闭。修复了非测试任务无条件写死 `codex_local`、能力接口写死可用、本地 Provider 未进入 dispatcher、Worker 阶段和候选物化分支等接线缺陷；服务器配置现在决定实际 Provider，客户端不能选择 endpoint 或 model。现有作业的旧 `codex_local` 任务不会被静默改写，页面会显示“本地 AI 已可用”并提供“使用本地 AI 重新整理”。后端生成专项 `33 passed` 且数据库守卫为 `ahamark.db unchanged`，前端专项 `36 passed`，Ruff、strict mypy `136 source files`、TypeScript、ESLint 和本机 production build均通过；本地 API、Worker、Web、PostgreSQL、Redis、MinIO 与 Qwen 共七项服务健康，`/ready` 显示生成 Provider `local_openai_compatible/available`、Worker 为 1。该能力只生成候选，教师确认与发布边界不变；当前只启用本机，未更新 node2。

2026-08-19 当前产品规则确认：AI 页面整理、题目抽取、答案和评分标准生成是创建作业的必经流程。生成任务及抽取、答案/Rubric、验证三个关键阶段未完成时，前端锁定“核对内容”和“确认发布”，直达步骤也会返回准备页；服务不可用时只允许恢复或重试，不允许手动跳过。中央审查和发布服务同时拒绝非 `review_required/ready` 的生成任务，避免绕过页面直接调用接口。AI 仍只生成候选，进入核对后的教师确认以及发布前完整性检查保持不变。前端专项 `37 passed`、全量 `45 files / 263 tests`，中央审查发布后端 `15 passed` 且数据库守卫为 `ahamark.db unchanged`；Ruff、Prettier、ESLint、TypeScript 和 33 页 production build 均通过，Next 构建仍只有既有 SWC lockfile 联网补丁告警。本地 API、Worker、Web 已重建，七项 Compose 服务健康，`/health`、`/ready` 和当前作业编辑页均为 HTTP 200，生成 Provider 为 `local_openai_compatible/available`、Worker 为 1；本轮尚未更新 node2。

2026-08-20 修复本机真实作业一键生成链路。当前作业实际已有两份 PDF、三页和完整 OCR；页面曾显示“0 个文件”是最新失败生成修订没有文件分析记录，并非上传丢失。本地模式现以确定性逻辑完成基本信息和文件/页面预检，只把判定为试卷的页面交给 Qwen；题目抽取及答案/Rubric 使用紧凑 JSON 对象合同，返回后再由 Pydantic 完整 Schema、实体引用和业务语义校验，外部 Provider 仍使用严格 JSON Schema。该调整绕开 llama.cpp 对复杂 grammar 的不兼容和约十倍生成降速；本地调用超时为 900 秒、Worker 上限为一小时，5 题抽取在真实样卷上约两分钟完成。建议题目会直接物化为可编辑草稿并进入答案阶段，本地每题一次调用同时生成答案和 Rubric；多余字段、中文类型、带单位分值、错误块 ID、Markdown 包装及残缺 JSON 均有白名单容错，评分点会按题目总分归一化，所有降级结果仍明确标记 `manual_required`。真实复验最终得到 5 道物化题目、5 份当前答案和 5 份当前 Rubric，任务为 `review_required`；验证为 `unsupported=5, failed=0`，没有发布作业或写入学生分数。生成专项 `55 passed`，数据库守卫为 `ahamark.db unchanged`，Ruff、补丁空白检查及改动文件 strict mypy 通过。本轮仅更新本机，未登录或部署 node2。

2026-08-18 用户要求由 Codex 全程完成、不接第三方在线 Provider，并授权继续开发。当前工作树已接入两个只在 `local-ai` Compose profile 中启用的内网服务：固定 `PaddlePaddle/PP-FormulaNet_plus-M` 公式模型（revision `712e6e2e4c313b1ea163be5c350127b82662c58d`）和固定 `Qwen/Qwen3-4B-GGUF` 的 `Qwen3-4B-Q4_K_M.gguf`，由官方 llama.cpp CPU server 提供 OpenAI-compatible JSON Schema 接口。两者均不发布宿主端口，运行时不下载模型，模型卷只读；应用只允许显式 host allowlist 的 Compose HTTP，拒绝 IP、metadata host、localhost 和未授权外部端点。评分、Stage 4 AI grading 与作业生成只产出 suggestion，继续要求教师复核，外部 Provider 请求在 node2 Compose 中固定关闭。

模型获取脚本固定 URL、revision、大小和 SHA-256，下载到 `.part` 后验签再发布。公式清单 SHA-256 为 `19bb16d0ba17771ce24dfce716d9f10f80c3df626ecc9b960283e28810190018`，Qwen GGUF SHA-256 为 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`；上传包外层及全部内层哈希均通过。node2 容量为 192 个逻辑 CPU、约 270 GB Docker 可见内存、`/data` 约 7.38 TB 可用。最终部署使用 RapidOCR 专用 API `ahamark/api:0594d10`、Web `ahamark/web:0594d10`、Formula `ahamark/formula:0594d10` 和由固定 digest 验证后加本地标签的 llama.cpp；迁移 `0048 -> 0049`、Formula/Qwen 健康、滚动切换和公网 `/`、`/health`、`/ready` 均通过。最近完整备份为 `/data/shr/ahamark-backups/pre-local-ai-0594d10-20260818T155113Z`；前两次运行分别因公式模型目录路径和 UID 999 只读权限门禁失败并完整回滚，最终以新卷、目录 `0555`、文件 `0444` 部署成功。公式和评分/生成只提供候选，继续要求教师确认；这不是识别准确率或无人复核自动判卷证据。

2026-08-18 已获用户授权自主完成后续开发与部署。production-safe RapidOCR bundle 接线已经完成本地开发和验证：默认 API 镜像继续关闭 OCR，node2 专用 `Dockerfile.rapidocr` 固定 `rapidocr==3.9.2`、`onnxruntime==1.28.0`，并把 wheel 内三份 ONNX 复制到固定目录；清单固定模型路径、大小、SHA-256、运行时版本、bundle/license approval UUID，清单 SHA-256 为 `f84336fc78cb51cd0ee223ee3c04158eb2f968af6fa8ffd31051b821f843ff5b`，NOTICE 明确仅批准本地印刷体文字 OCR。运行时下载仍被配置和代码双重禁止，启动/readiness 校验清单与模型，推理前再次检查文件身份，异常稳定 fail-closed。node2 Compose 的启用参数来自 `runtime.env` 且默认关闭，旧 `runtime.env` 与 `5eda608` 回滚路径保持兼容。

真实候选镜像在 `--network none` 下用合成印刷体图片完成一次离线推理：readiness 为 true，返回 2 个文字块并识别出 `AhaMark` 与 `123`；这只证明固定镜像链路可运行，不是准确率。该验证同时发现 RapidOCR v3 的 boxes 为 NumPy 数组，适配器现以有界形状检查后转换，并新增回归测试。后端全量唯一计数为 `1052 passed, 19 skipped`，零真实失败；三组初跑的 15 个失败均为外置 `--basetemp` 被数据库安全守卫拒绝，修正进程 `TEMP/TMP` 后相关 10 文件 `50 passed, 1 skipped`，所有运行均为 `ahamark.db unchanged`。全仓 Ruff、strict mypy（127 个源文件）、Alembic 单 head `0049_usernames` 通过；前端 Prettier、ESLint、TypeScript、`38 files / 234 tests` 和 production build 19 页通过；node2 Compose 在 OCR 默认关闭和显式固定 bundle 开启两种配置下均通过 `config --quiet`。实现已以 `789a59d` 提交并推送；该段记录的是当时尚未部署的候选状态，后续已由 `0594d10 + 0049` 正式替代。

2026-08-18 尝试部署 `056f039`：镜像双层 SHA-256、Compose、Nginx、`0048 -> 0049` 迁移、用户名回填和新 API-A/B 健康均通过；既有 1 个用户回填后 username 空值和重复数均为 0。切换后公网 `/ready` 暴露文字 OCR 从 available 降为 unavailable，因此按失败门禁将 API/Web/Worker 和 `runtime.env` 滚动恢复为 `5eda608`，公网 `/`、`/ready`、1 个 Worker 和文字 OCR 随后恢复 available，登录页也恢复旧邮箱界面。经用户单独授权并在新建、验证 `0049` PostgreSQL 备份后，数据库已事务性 downgrade 到 `0048_class_resources`，`users.username` 列确认不存在，旧 `migrate` 容器以 0 退出；node2 已完整恢复原应用/schema 基线。

根因不是简单漏装依赖：`056f039` 的生产 `provider_from_settings()` 对 RapidOCR 不接入 engine factory；仓库虽有 artifact/runtime 离线验证组件，但没有 production bundle wiring，符合“RapidOCR runtime/download hard-off”边界。曾构建一个 `.[ocr]` 本地候选，确认只装包仍不能恢复 Provider 后已撤销 Dockerfile 改动，候选未上传、未部署、未提交。恢复文字 OCR 需要单独设计经审计的固定 artifact 接线和测试，不能在服务器临时安装或启用运行时下载。

2026-08-18 随后对 `789a59d` 执行受保护部署。上传归档和候选 Compose 完整；服务器独立只读诊断确认 API/Web 镜像 ID 分别为 `sha256:1fa11b99a46d38ee2b1048d937537c6cc2d543258871b605f48cf52bf9d81d6b`、`sha256:acd8f0e9f8d7ea5c08f0b35ff9479b93ef5bff0f99d6eba4f8407e6fbd5abd80`，镜像内 head 为 `0049_usernames`，候选 runtime 明确启用固定 OCR，Compose 解析出的应用镜像均为 `789a59d`。部署自动化先后暴露 SSH stdin、Windows CRLF 与 Compose 子进程继承输入造成的提前结束；修复为服务器临时文件执行后，最终尝试仍在脚本 `image_validation` 阶段中止。最终尝试没有进入 `0048 -> 0049` 迁移，自动回滚确认数据库仍为 `0048_class_resources`、应用仍为 `5eda608`。按操作约定已停止继续尝试，不能把 `789a59d` 描述为已部署。

管理员账号登录已经从邮箱改为用户名：

- `User.username` 唯一、索引化，登录时执行 NFKC、转小写和格式校验。
- 用户名为 3–64 位，以字母或数字开头，只允许小写字母、数字、点、下划线和连字符。
- production `/auth/login` 只接受用户名；邮箱载荷返回统一认证失败，不泄漏账号是否存在。
- Web 登录页只显示用户名和密码，不提供公共注册、自助找回或账号申请。
- 管理员、教师和学生账号可由服务器交互式 CLI 引导创建；日常账号运维由管理员专属页面完成，密码输入不回显且数据库只保存 scrypt 哈希。
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

1. `0050`–`0053`、自愿改密、学生提交/复核/资料/学习分析已部署 node2 并完成 PostgreSQL、Compose 和公网只读验收；完整后端长耗时套件没有跑完，三类账号新页面的带登录浏览器自动化闭环仍可补充。
2. 公网入口仍使用自签名证书；手机 Safari 登录和完整教师流程尚未验收。
3. 公网端口无来源限制；若后续恢复“仅校园网”目标，需要由 iKuai/防火墙实施边界并做内外双向实测。
4. 私有 OCR/Gold 的两页修复输出位于仓库外，未合并或覆盖原 60 页草稿；保持暂停。
5. 真实 OCR、手写、公式、复杂版面和真实 Provider 质量没有生产证据。
6. 全离线公式 OCR 与本地 Qwen 候选服务线上状态为 available，但仅有合成链路和健康证据；不得把 available 或单次合成推理称为真实准确率、可靠手写识别或可无人复核自动判卷。

### 下一步顺序

本轮 UI 与生成稳定性修复已部署 node2：整理试卷面板位于向导顶部并支持整体收起；隐藏生成/答案/Rubric 历史记录展示；隐藏旧的整理页面核对卡和独立题目列表入口；评分项改为“步骤（分值，必要/可选）”紧凑显示；文件用途确认已并入上传卡片并固定教师提供的答案来源；Provider 不可用时实时显示不可用并停止剩余生成。相关前端与后端测试、生产构建和 node2 独立公网复验均通过。

下一步强制刷新浏览器后，用合成或用户明确授权的测试试卷验证线上整理试卷全流程，并补充三类账号新页面的带登录浏览器自动化闭环；本地学习助手保持默认关闭，评分、生成和公式结果继续要求教师确认。

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

## 仓库导航

| 路径                             | 内容                                 | 维护入口                               |
| -------------------------------- | ------------------------------------ | -------------------------------------- |
| `apps/`                          | API、迁移、CLI 与 Web 应用           | [apps/README.md](apps/README.md)       |
| `workers/`                       | Celery 异步任务                      | [workers/README.md](workers/README.md) |
| `tests/`、`test_support/`        | 后端测试、fixture 与数据库隔离保护   | [tests/README.md](tests/README.md)     |
| `scripts/`                       | 浏览器验收、容量、评测和运维辅助脚本 | [scripts/README.md](scripts/README.md) |
| `docs/`                          | 稳定说明与脱敏验收证据               | [docs/README.md](docs/README.md)       |
| `deploy/`、`docker-compose*.yml` | 镜像静态资源和环境编排               | [deploy/README.md](deploy/README.md)   |
| `data/`                          | 可版本化的公开或合成评测数据         | [data/README.md](data/README.md)       |

根目录只保留项目入口、依赖清单、Compose 文件和仓库规则。数据库、模型、日志、缓存、浏览器输出及本地评测构建均为运行产物，不属于源码结构。

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
python -m app.cli.create_admin --username admin01 --display-name "管理员"
```

命令会在终端中两次询问密码且不回显。三类新账号分别获得唯一的 `teacher`、`student` 或 `admin` 角色；已有无角色账号继续按历史教师兼容。管理员登录后自动进入 `/admin/accounts`，可创建账号、用 CSV 批量创建教师和学生、批量启停或强制下线、按筛选导出账号、查看安全概览与活动会话、撤销非当前会话、查看账号操作审计、修改展示姓名和重置他人密码；停用与密码重置会强制旧会话退出。教师在班级页按用户名或账号姓名选择学生账号并直接关联，学生档案邮箱仅作可选联系信息。为避免业务数据悬空，管理端不提供物理删除和账号类型直接变更。仓库不提供公共注册、自助找回或账号申请。`DEMO_ACTOR_ENABLED` 只允许非 production 开发环境使用。完整操作边界见 `docs/ADMIN-ACCOUNT-OPERATIONS.md`。

注意：node2 已部署用户名版本和 `0049_usernames`，但未创建或验收管理员用户名账号；不要把迁移完成等同于登录业务流程已验收。

## 教师主流程

1. 创建作业，填写名称、截止时间和发布班级。
2. 一次选择或拖入多个 PDF/PNG/JPG；前端先做格式、空文件和单文件大小校验，再按顺序上传。
3. 系统整理试卷并生成题目、答案和 Structured Rubric 候选。
4. 教师逐题核对；必要时旋转页面、手动框选或追加跨页区域。
5. 发布检查只接受当前 active paper、完整分值、确认答案和 active Structured Rubric Set。
6. 已发布作业可创建批改批次并上传学生答卷。
7. 系统匹配学生、处理页面并生成识别与评分建议；歧义、低置信、stale 或 Provider unavailable 均进入人工复核。
8. 教师接受或修改建议，最终生成版本化成绩快照，再明确发布成绩记录。
9. “错题与练习”汇总每个作业/班级最新正式发布版本中的真实错题；教师筛选和复核后，可进入新建作业流程组织针对性练习。

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

教师错题视图同样只消费 `GradeRelease` 固定的正式快照，并且每个作业/班级只取最新发布版本；它不会读取可变的 AI 建议或临时评分结果，也不会自动生成或发布练习。

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

| 日期       | 提交/状态        | 结论                                                                             |
| ---------- | ---------------- | -------------------------------------------------------------------------------- |
| 2026-08-19 | `3142998` 本地   | 管理员批量运维、安全面板、会话撤销与登录失败限流修复完成；未 push、未部署        |
| 2026-08-19 | `855d9fb` 本地   | 全仓导航、临时产物隔离和结构硬化检查完成；未移动本地资料，未 push、未部署        |
| 2026-08-19 | `4e0dd57` 本地   | CSV 批量建号、账号审计与 9 场景管理员浏览器验收通过；未 push、未部署             |
| 2026-08-19 | `9bccb91` 本地   | 三类账号运维及教师复核改进已快进整合至 D 盘当前分支；未 push、未部署             |
| 2026-08-19 | `7e88fae`        | 功能分支与最新远端历史正常合并并推送 `master`；代码树相对首父无变化              |
| 2026-08-18 | `0594d10` 已部署 | `0049`、固定文字/公式 OCR、本地 Qwen 建议服务上线；公网 readiness 全部 available |
| 2026-08-18 | `789a59d` 未部署 | 固定 RapidOCR bundle 已推送；部署脚本在 image validation 中止并回滚到旧版        |
| 2026-08-18 | node2 完整回滚   | `056f039` 因文字 OCR unavailable 回滚；应用/schema 恢复 `5eda608 + 0048`         |
| 2026-08-17 | `9b129bc`        | 管理员发布用户名账号；当时未部署，后随 `0594d10 + 0049` 上线                     |
| 2026-08-17 | `0da6dd9`        | node2 允许公网 IP Host 与 origin；已部署                                         |
| 2026-08-17 | `cfe2752`        | Rootless Docker 将唯一 Nginx 入口改为宿主 13300；已部署                          |
| 2026-08-17 | `f050618`        | 拒绝 Codex 助手元话术草稿；未部署                                                |
| 2026-08-17 | `4a2cf2a`        | 私有识别快速正文核对模式；未部署，私有工作已暂停                                 |
| 2026-08-15 | `5eda608`        | node2 旧回滚镜像基线；schema 为 0048                                             |
| 2026-08-07 | `5ae3a78` 及后续 | 选择性实现手动切题和当前 Structured-only 教师流程；没有整分支合并合作者代码      |

GitHub 合并结论：产品代码此前通过 PR #2 进入 `master`；后续离线 Provider、部署账本及两条分支历史已由 `7e88fae` 正常合并。Draft PR #1 的 head 已是祖先，不重复合并；`gyh--001` 只按功能选择性吸收，旧迁移链、外部 OpenAI 调用、依赖漂移和降低 CSP 的部署改动仍不得整分支合入。

本次操作未创建账号、未处理私有识别材料、未发布作业或成绩。2026-08-19 最后一次外部独立检查确认 `/`、`/health` 均为 HTTP 200，`/ready` 为 `ready=true`，Worker 为 1，文字 OCR、公式 OCR、主观评分、AI 评分和作业生成 Provider 均 available；后三类为本地 Qwen suggestion-only，公式和评分继续明确要求教师确认。这只是运行状态和合成链路证据，不是识别准确率或自动判卷验收。最终备份及旧 `5eda608 + 0048` 回滚路径均保留；一次疑似 SSH 密码误发后已由用户在可见 `passwd` 终端轮换，账本不记录任何凭据。
