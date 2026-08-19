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

| 项目           | 当前事实（2026-08-19）                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------ |
| 正确工作区     | `D:\OpenAIData\Workspaces\AhaMark`                                                                           |
| 工作分支       | `codex/grading-confirm-results`；账号运维第二阶段提交为 `3142998`                                            |
| 应用基线       | 本地修复提交 `e375e55`；node2 API/Worker 为 `e375e55`、Web 为 `c444019`；GitHub 主线仍为 `4f4a374`           |
| 远端状态       | 本地功能提交尚未 push；node2 已完成受保护部署并通过独立公网复验                                              |
| 数据库迁移     | 本地与 node2 均为 Alembic 单 head：`0053_disable_forced_password_change`                                     |
| 最新开发       | 本机作业生成已接通固定 Qwen 本地模型；旧任务可一键重新整理，AI 仍只生成待教师确认的建议                      |
| 本机业务验收   | 隔离合成环境 A–F 已通过并停在成绩发布前；公式不可读/重框专项通过；GradeRelease 为 0                          |
| node2 在线版本 | API/Worker `e375e55`、Web `c444019`、schema `0053`；文字、公式 OCR 与本地建议 Provider available             |
| node2 入口     | `https://222.195.89.236:13300`；自签名证书；公网可达，无来源白名单                                           |
| 部署范围       | 只发布 Nginx `0.0.0.0:13300 -> 8443`；数据库、Redis、MinIO、API、Web、Worker、Docker socket 均无宿主发布端口 |
| 私有识别工作   | 暂停；不得继续处理、上传或提交私有 OCR/Gold、图片、正文或来源映射，除非用户再次明确授权                      |
| 主线合并       | `7e88fae` 仅汇合历史，相对已验证代码树无文件差异；未强推，远端主线与功能分支一致                             |

### 当前开发事实

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

2026-08-19 改进教师作业整理的降级体验：Codex 页面整理、题目抽取、答案和评分标准生成均明确标记为“可选”，Codex 未连接时不再显示“等待 Codex 完成”，而是提示教师可继续手动核对，并提供“一键进入核对内容”的入口；原有单阶段重试仍保留。该改动不伪装后台任务，也不改变正式题目、答案和评分标准必须由教师确认的边界。生成面板与作业向导专项 `35 passed`，Prettier、TypeScript、ESLint 和 33 页 production build 均通过；Next 构建仍只有既有 SWC lockfile 联网补丁告警。该改动当前仅在本地，尚未部署 node2。

2026-08-19 随后在本机启用真实的离线作业生成辅助：固定 `Qwen3-4B-Q4_K_M.gguf` 已下载到 AhaMark 专用 Docker 卷，文件大小 `2497280256` 字节且 SHA-256 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` 验证通过；llama.cpp 服务仅暴露在 Compose 内网，模型卷只读，外部 Provider 请求保持关闭。修复了非测试任务无条件写死 `codex_local`、能力接口写死可用、本地 Provider 未进入 dispatcher、Worker 阶段和候选物化分支等接线缺陷；服务器配置现在决定实际 Provider，客户端不能选择 endpoint 或 model。现有作业的旧 `codex_local` 任务不会被静默改写，页面会显示“本地 AI 已可用”并提供“使用本地 AI 重新整理”；仍可选择完全手动核对。后端生成专项 `33 passed` 且数据库守卫为 `ahamark.db unchanged`，前端专项 `36 passed`，Ruff、strict mypy `136 source files`、TypeScript、ESLint 和本机 production build 均通过；本地 API、Worker、Web、PostgreSQL、Redis、MinIO 与 Qwen 共七项服务健康，`/ready` 显示生成 Provider `local_openai_compatible/available`、Worker 为 1。该能力只生成候选，教师确认与发布边界不变；当前只启用本机，未更新 node2。

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

下一步保持 node2 当前 API/Worker `e375e55`、Web `c444019` 与 schema `0053`，补充三类账号新页面的带登录浏览器自动化闭环，并在方便长时间运行时完成剩余后端全量。后续部署仍必须先备份；本地学习助手保持默认关闭，评分、生成和公式结果继续要求教师确认。

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
