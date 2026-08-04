# AhaMark

## 接手必读：当前状态、问题与下一步

2026-08-04 教师实测“点击后没反应”改进专项：从远端 Draft PR #1 head `609673089c0e8e1f9fb35cd815e0cf7b3bbb80f9` 创建干净隔离 worktree；接手时 PR 仍为 Draft/open、base `master`，无评论、审查或检查更新，本轮没有改动正在运行的测试数据库、Docker、正式答案/成绩或发布状态。实测接口证据表明，“批量接受可用评分标准”实际 HTTP 200 但 `accepted_count=0`，旧响应不报告考虑了哪些候选及为何跳过，前端又无条件显示成功；五份合成评分标准的 criterion `validation_rule={}`，因此均被 `RUBRIC_VALIDATION_CONFIG_INVALID` 阻断。同时中央核查在正式答案/评分标准未完成时已允许点击“生成兼容版本”，后端只能返回 422，形成第二个看似无反应的入口。这里的候选来自明确标记的 `codex_simulated` 测试资料，不宣称是真实外部 Provider 输出质量。

本轮后端为答案与评分标准候选返回服务端权威的 `server_eligible` 和 `ineligibility_reasons`；两个批量接口统一返回 accepted/considered/skipped 数量、ID、题号和原因码，只统计仍为 `suggested` 的当前候选，继续保持未知来源、低置信、缺证据、人工复核、结构无效、非 deterministic 或验证 indeterminate 时 fail-closed。前端对零接受和部分接受分别显示明确计数、题号、中文原因与修复动作，不再把“接受 0 项”显示为成功；候选卡片直接解释为何不能自动接受。新增字段按可选字段兼容旧缓存/滚动升级，服务端未明确返回 `false` 时不会使旧页面数据崩溃。中央核查只有在非 binding blocker 清零且正式答案、评分标准确认完成后才自动或手动创建兼容 binding；前置条件不满足时按钮禁用并提示先完成哪些内容。AI 仍仅为 suggestion-only，未增加任何自动教师确认、正式成绩写入或发布路径。

最终验证（2026-08-05）：后端全量使用唯一系统临时目录 `C:\\Users\\Lenovo\\AppData\\Local\\Temp\\ahamark-provider-feedback-full-20260804-01` 且禁用 cacheprovider，结果 `579 passed, 18 skipped, 339 warnings`，无失败或 setup error，耗时 `44:49`，`ahamark.db` 守卫 unchanged；18 个 skip 均为仓库既有条件型用例。答案/评分标准生成专项 `18 passed`；前端相关组件 `78 passed`，前端全量 `27 files / 186 tests passed`；Prettier、ESLint、TypeScript、Next production build（19 个静态页面）、全仓 Ruff check、仓库标准 strict mypy（107 source files）、Alembic 单一 head `0033_joint_exam_class_authorization` 与 `git diff --check` 均通过。Next build 输出仓库既有的 SWC lockfile 自动修补警告，但构建退出码为 0，构建前后根 lockfile 均无 diff，Web lockfile 仍不存在。依赖安装仅用于隔离 worktree 验证；npm 报告既有 4 个 high advisories，未运行会改锁文件的自动修复。未运行 fresh browser E2E，不能把组件回归夸大为浏览器端到端验证；当前 `localhost:3300` 仍是旧测试实例，本轮不部署或替换它。本专项 7 个产品/测试文件加 README 将按用户授权精确暂存、提交并安全推送到现有 Draft PR #1，仍不合并、不部署、不改变 PR 状态。

2026-08-04 独立提交前复审阻断跟进：复核确认旧 release 公开与 confirm-results 原先没有共同事务序列点；现统一先执行受 owner 限制的 assignment 自更新，PostgreSQL 获得行写锁、SQLite 获得写事务锁，且不改变 assignment 内容指纹，锁顺序统一为 assignment → narrower rows。公开在共同锁内重新读取 release 并保留“更高正式版本则 409”的历史审计语义。联考只读入口也已在查询 metadata 前要求 active teacher，撤权后返回 `403 TEACHER_ROLE_REQUIRED`。第二次复审指出原并发回归可能把慢调度误判为锁生效；测试现先确认真实 HTTP confirm 已发起，再监听 pytest 隔离数据库 engine 的 `before_cursor_execute`，确认请求实际发出 assignment serialization SQL 后，才验证 publish 持锁期间 `_confirm_results_state` 不可达。三组证据分别为：

- 单次聚焦回归：`1 passed`。
- 稳定性回归：10 个独立 pytest 进程、10 个独立 basetemp，`10/10 passed`。
- mutation/bypass 反证：一次性 pytest collection hook 绕过共同锁，结果 `1 failed`；失败点为未观测到 assignment serialization SQL（`confirm did not reach the assignment serialization SQL`）。绕锁逻辑只存在于该一次性测试进程，未写入工作区。

相关产品修复扩展专项保持 `88 passed`；完整 confirm-results、学生端和 results 发布专项为 `29 passed`、无 skip/error、耗时 `1:56`。上一轮后端全量 `576 passed, 18 skipped` 仅作为基线，本轮未重跑约 34 分钟全量，不能表述为本轮全量通过。全仓 Ruff check、本轮测试文件 Ruff format-check、strict mypy `107 source files` 与 `git diff --check` 均通过；Alembic 唯一 head 为 `0033_joint_exam_class_authorization`，根 lockfile 和 `0031_student_portal.py` blob 与 HEAD 一致，`ahamark.db` 与 Web lockfile 不存在，暂存区为空。没有产品、前端、模型、迁移或依赖改动；最终 README 澄清轮只修改本账本，未暂存文件总数仍为 13。

2026-08-04 PR #1 独立审查修复专项：本 worktree 从 Draft PR #1 的远端 head `6d0941bd1d4ecb40810f4f6927d7e9f2967083e1` detached 起步；开始修改前已确认工作区/暂存区干净、PR 仍为 Draft 且没有新评论/审查/检查或协作者更新，Alembic 唯一 head 为 `0033_joint_exam_class_authorization`，Docker daemon 未运行，`ahamark.db` 与 Web lockfile 不存在且根 lockfile 无改动。五项 finding 均先独立复现，再按 fail-closed 边界修复：

- 分题协作者的 review workspace 只返回其被分配答案通过 `StudentAnswerRegion` 或 `GradingEvidence` 明确映射的页面；映射不完整时不返回页面，协作者始终没有 `original_url`，无可见答案的 submission 也不进入响应。回归覆盖已分配/未分配/未映射页面、整份原文件 URL 和任意 unrelated `submission_id`。
- 正式成绩统一选择每名学生非 `voided` submission 中最大的 `attempt_number`，最新 attempt 未完成时不得回退旧 attempt；未绑定 submission 仍保留为发布 blocker。readiness、snapshot、release item、学生端历史脏数据防御和 analytics 由同一规则保证唯一计数；split 回归确认 attempt 递增。
- 作业文件删除改为两阶段：先把精确授权的 `ready` 文件持久化为 `pending` 并保留页面授权关联，再只删除该 `storage_key`，最后事务删除页面、重编号并标记 `deleted`。准备提交失败不会碰对象；对象失败或最终数据库提交失败均保持可重试状态，同一路由可幂等收口。没有 Bucket、前缀或未知对象删除，也没有新增迁移。
- 若同 assignment/class 已存在更高的正式 released version，旧 release 首次公开返回 `409 GRADE_RELEASE_SUPERSEDED`；学生端当前成绩按 release `version` 优先而不是公开时间，历史版本仍可审计。
- 联考和批改邀请显式要求 active teacher；无角色、student-only、其他角色均拒绝。既有协作记录、联考邀请列表、权限入口和协作 metadata 也会重新校验 active teacher，角色撤销立即失权。AI 仍仅生成建议，正式成绩仍只由主教师明确确认，学生仍只能读取账号绑定且明确公开的正式成绩。

2026-08-04 browser E2E 契约跟进：后端全量中的两个失败不是上述五项修复造成的业务回归。Git blame 显示 `scripts/business_browser_e2e.mjs` 在 `99dd4fbb` 已同步“简化作业复核确认”产品语义，但 `test_business_browser_e2e_contract.py` 的两条断言仍停留在 `2b56046b`：其一仍要求所有 `suggested` 文件分析都人工确认，实际契约是高置信、无冲突项以 `system_auto` 采用，只有不能自动采用的 suggested 项进入人工按钮；其二仍要求逐条处置生成建议，实际契约是页面组织和题目抽取建议只读审计、`writes=[]` 且 `teacher_action_required=false`。测试现改为验证自动采用资格、冲突项人工确认、API 状态审计及无 disposition 写入；没有删除发布门禁断言，没有修改产品/API、扩大权限或改变成绩发布边界。该契约文件 `39 passed`；为满足改动文件 Ruff format 门禁，同一文件两个起点已有的单引号断言被格式化为双引号，属于无语义机械变化。

最终验证使用唯一系统临时根 `C:\Users\Lenovo\AppData\Local\Temp\ahamark-pr1-review-9f3fd76b9fc0487e9cb1083b3082694a`，每组使用独立 `--basetemp`，并禁用只写工作区 `.pytest_cache` 的 cacheprovider，从而避开 Windows 旧 temp/cache 权限噪音。正确 worktree cwd 的后端全量为 `576 passed, 18 skipped`，耗时 `34:08`，`ahamark.db` 守卫 unchanged；18 个 skip 均为仓库既有条件型用例，其中 15 个需要显式隔离 PostgreSQL 环境、1 个受 Windows symlink 权限条件保护、2 个需要真实 RapidOCR 运行时，没有 test error。五项 finding 的 assignments/collaboration/confirm-results/joint-exam/student-portal/submission/results/analytics/authorization 扩展专项为 `74 passed`；browser E2E 契约文件为 `39 passed`；迁移命名测试为 `33 passed, 1 skipped`。全仓 Ruff check、11 个改动 Python 文件的 Ruff format check、strict mypy `107 source files` 与 `git diff --check` 均通过。最终只读复核确认根 lockfile 与 HEAD blob `e31e8009...` 一致，`0031_student_portal.py` 与 HEAD blob `517d2f0f...` 一致，`ahamark.db` 与 Web lockfile 不存在，Alembic 唯一 head 为 `0033_joint_exam_class_authorization`；本地仍为 detached `6d0941bd...`，远端 Draft PR #1 head 相同且没有新评论、审查、检查或协作者更新，暂存区为空。当前已具备独立提交前复审条件，但本轮仍未获暂存、提交或推送授权。

剩余风险 / 下一位接手：证据映射不完整会有意隐藏协作者页面；改善可用性必须先建立可信题目到页面映射，不能回退整卷授权。对象删除失败后的 `pending` 当前依靠显式重试收口，没有后台 outbox worker。两个 browser E2E 基线契约已按当前真实产品语义修复且全量测试完成。本专项只修改 `assignments.py`、`grading.py`、`results.py`、`student_portal.py`、七个对应后端测试和本账本；没有前端、模型、迁移或依赖修改。仍禁止暂存、提交、推送、合并、部署或改变 PR 状态。

更新时间：2026-08-04。当前专项基于 `2b24ab4001e9f36b0ea32337331a60f53c3d33c1` 的独立 worktree，保持 detached HEAD；对应本地起点分支为 `codex/grading-confirm-results-update`，远端 Draft PR #1 仍以 `master` 为 base 且未合并。禁止在未获再次授权时暂存、提交、推送、合并或部署。

本轮修改及原因：

- 中央核查不再读取 `GenerationIssue` 或 `risk_summary` 生成当前待办；两者只保留为生成历史审计。原因是旧生成失败即使内容已恢复，也不能计入老师当前问题数量或阻塞发布。
- 当前中央问题由数据库中的班级、题目、文件/页面分析、正式参考答案、结构化评分标准和当前投影重新计算；每条新问题记录附带对象、原因、发布影响、修复动作、步骤和锚点。
- 文件、页面、缺分和总分冲突文案使用文件名、页码、题号与实际分值，避免只显示泛化错误码或内部 UUID。
- 移除中央问题列表中的 `LEGACY_CONVERSION_REVIEW` 重复项；有损投影只在“评分标准兼容说明”产生一次有针对性的人工决策，无损投影继续自动完成。
- 生成区默认只显示进度和草稿状态；`risk_summary` 与 `GenerationIssue` 数量移入折叠的“生成记录/技术详情”，并明确不是当前发布待办。
- 新增前后端回归断言，覆盖历史生成问题不进入中央队列、技术历史默认折叠，以及教师可见问题文案契约。
- fresh Docker 验收发现历史 `0007` 迁移会通过当前 ORM 元数据提前创建 `grade_releases.student_visible_*`，随后 `0031_student_portal` 重复加列而使全新数据库无法升级。用户已于 2026-08-04 明确授权最小历史迁移例外：`0007` 现改为其首次入库提交 `f7783f0` 中实际生成的五张固定表定义，不再导入当前 `Base.metadata`；没有带入 `0008+` 的字段、外键、索引或约束。历史模型与固化定义编译出的完整 PostgreSQL DDL SHA-256 均为 `bf74e6ca6bd83f6fa8cc8175f819d90c17b62a565c90eb79ef498971c4626855`。`0031` 继续保持 HEAD 原文且不作为兼容修复点。
- `0007` 固化保留当时跨方言语义：JSON 字段在 SQLite 为 JSON、在 PostgreSQL 为 JSONB，且没有把 ORM 客户端默认误写成 server default。新增回归覆盖固定字段/索引契约、SQLite 升降级往返、唯一 head，以及仅在显式本地隔离数据库名和 marker 同时匹配时运行的 fresh PostgreSQL `empty → head → head` 与 `empty → 0030 → head` 路径；隔离 PostgreSQL 全路径为 `3 passed`。
- 中央核查前端修复自动化闭环：`setAutomating(true)` 与 Bundle reload 都会触发 effect cleanup，旧逻辑的 `!cancelled` 同时阻止成功后的 reload 并永久保留 `automating=true`，使后续无损 binding 或 ready Bundle 无法显示；现在成功回调与 finally 均由 assignment epoch 防串写，当前作业可以完成“自动核对 → 自动 binding → reload ready”，新增回归测试验证完整调用链。
- PR 审查进一步发现同一 cleanup 也会静默吞掉自动核对或无损 binding 的失败提示，且已记录的 attempt key 会让同一会话无法重试。两个自动 effect 现使用独立 automation request generation 配合作业 epoch 判断成功、失败与 finally 是否仍属当前请求；失败会显示针对性 toast 并解除忙碌，但保留 attempt key 防止无限自动重试。教师点击“重新扫描最新状态”会显式清除两个 attempt key 并重新扫描，从而提供有界主动重试；旧作业迟到失败不能污染新作业。
- 最终复审 P2 发现组件直接卸载时 assignment epoch 不变，主加载 effect cleanup 原先没有失效 automation request generation，pending 自动核对或 binding 的迟到回调仍可能跨页面 toast 或 reload。cleanup 现同步递增 automation generation；自动操作在卸载后无论成功或失败都不能写状态、提示或重新加载，同时保留作业切换保护、当前页面失败提示和主动重试链。
- 浏览器 E2E 脚本同步新版创建/生成入口与 `system_auto` 判定：高置信无冲突用途不再被脚本误当作人工确认；合成教师已有完整正式答案和 Rubric 时，未采用的 AI suggestion 只保留审计，不为了发布逐条拒绝。

已解决：历史生成问题冒充当前待办、恢复后的旧问题仍显示计数、兼容损失在生成区与中央区重复要求操作、生成区默认暴露历史风险数量、自动核对 reload 后无损 binding 被前端状态卡死，以及 `0007` 读取当前 ORM 元数据导致 fresh PostgreSQL 在 `0031` 重复加列。迁移修复已按 2026-08-04 的明确例外授权实施并通过新库/升级路径回归。

2026-08-03 失败复现：隔离 project `ahamark-5c49-migration-20260803-03` 使用全新 `postgres_data`、host port `55440`，仅执行原始迁移链。`alembic upgrade head` 在 `0031_student_portal` 稳定失败为 `DuplicateColumn: student_visible_at`；数据库回滚后再次仅升级到 `0030_collaborative_grading`，查询确认两列已被 `0007` 提前创建。真实根因是 `0007_grade_release_reports_analytics.py` 通过当前 `Base.metadata` 动态创建历史表，不是 `create_all`、stamp、旧卷或启动顺序；失败发生在 `0031`，新增 `0034` 技术上无法在失败点之后执行并挽救。

2026-08-04 授权后修复复验：隔离 project `ahamark-5c49-migration-20260804-05`、host port `55442`、数据库 `ahamark_migration_0007_20260804m7fresh` 与全新卷 `ahamark-5c49-migration-20260804-05_postgres_data` 运行受保护回归，结果 `3 passed`。完全空库 `upgrade head` 成功；已在 head 再次 `upgrade head` 幂等成功；仅重置该显式隔离库后，`upgrade 0030` 时 `student_visible_*` 均不存在，再升级 head 后两列、`fk_grade_release_student_visible_by` 和 `ix_grade_releases_student_visible_at` 各存在一次，唯一 head 为 `0033_joint_exam_class_authorization`。证据：`C:\Users\Lenovo\.codex\visualizations\2026\08\03\019fc78a-d972-75b1-bed0-62e54645f3b1\fresh-docker-e2e\migration-0007-20260804\result.json`。已在 head 的既有数据库由 `alembic_version` 标记当前 revision，`upgrade head` 不会重跑已完成的 `0007`，所以其表结构与数据语义不变；该例外只纠正未来从未执行 `0007` 的迁移链。

strict mypy 已使用同一 bundled Python 3.12.13、仓库根目录和标准命令 `python -m mypy` 对账：当前 worktree 与临时解包的基线 `2b24ab4` 均为 `Success: no issues found in 107 source files`。此前两个 Celery decorator 错误来自不同的显式文件/参数调用，不是仓库标准全量门禁，也不是本轮差异触发。

同一作业双 PDF 浏览器验收使用 fresh project `ahamark-5c49-review-20260803-04`，ports 为 Web `43301`、API `48801`、MinIO `49902/49903`、PostgreSQL `55441`，三组 project-scoped volumes 全新创建。因正式空库迁移仍被 `0007/0031` 阻塞，临时 override 仅在测试启动时先迁移到 `0030`、删除被 `0007` 过早创建的两列、再继续到 head；该垫片不在 Git 中，不是生产方案。API 容器后续为重建 Web 被 Compose 一并重建时垫片重复执行，导致本项目的 `student_visible_*` 再次被删除而 Alembic head 不会重补；因此 project 04 只能作为 assignment 业务流证据，不能作为最终迁移一致性证据。

同一合成作业 `2e6d42e6-fd05-4c6a-986b-43f00a87d313` 已完成：上传 `synthetic-question-paper.pdf` 与 `synthetic-third-party-answer-and-rubric.pdf` 均为 HTTP 201；Fake Provider 自动识别为 `question_paper 0.72` 与 `reference_answer 0.70 / third_party`，无角色冲突、无文件用途确认点击；生成并物化一题。Fake Provider 不具备可靠 PDF 分值/答案/Rubric 抽取能力，因此由已认证合成教师把明确标记为第三方的合成资料转录为正式答案与评分标准，没有配置或冒充官方 Provider。最新简化 Rubric 使用 `manual_only` 且不含扩展规则，binding 为 confirmed、`loss_report=[]`、实时会话 `blocking=0/warning=0/info=0`；页面显示“已自动核对”、不显示 `CONFIRM_*`，主教师只点击一次“确认发布”，两次写请求为 prepare-publication 200 与 publish 200，最终 assignment 为 `published`。这证明同一作业的 UI/HTTP/持久化编排与发布门禁，不证明真实 PDF 内容质量；“完全由 Provider 从两 PDF 自动抽取正式答案/Rubric 且零教师转录”仍未通过。

同一作业证据目录：`C:\Users\Lenovo\.codex\visualizations\2026\08\03\019fc78a-d972-75b1-bed0-62e54645f3b1\fresh-docker-e2e\same-assignment-two-pdf`。`pre-review.json` 保存发布前 ready Bundle 与无损 binding，`result.json` 保存一次最终发布及会话 0/0/0，`screenshots\two-pdf-generation.png` 与 `screenshots\ready-one-click.png` 分别保存自动识别和最终单击页面；临时脚本/override 位于 `C:\Users\Lenovo\AppData\Local\Temp\ahamark-5c49-review-20260803-04`。

最终验证（2026-08-04）：fresh PostgreSQL 专项 `3 passed`；全部迁移命名测试加两个单-head 契约 `35 passed, 1 skipped`（skip 为未注入隔离 PG 变量的同一用例，已在专项中通过）；中央核查后端 `11 passed`；最终复审 P2 修复后中央核查前端专项 `69 passed`、前端全量 `27 files / 183 tests passed`。Prettier、ESLint、TypeScript、全仓 Ruff check、标准 strict mypy `107 source files`、Next production build（19 个静态页面）及 `git diff --check` 均通过。P2 修复只改前端卸载防陈旧逻辑、测试和本状态账本，未改变后端或迁移，因此复用本提交父节点已通过的后端、Ruff、strict mypy 与迁移证据。Next 仍提示缺少可选 SWC lockfile 条目并尝试修补失败，但构建退出码为 0，根与 Web lockfile 均无 diff。`ahamark.db` 守卫在各 pytest 运行中均通过。

Docker 隔离状态：本任务仅启动并停止 `ahamark-5c49-migration-20260804-05` 的 PostgreSQL；容器为 `Exited (0)`，network/volume 保留，未执行 `down -v`、prune 或删除。其他 task 的 `ahamark-business-e2e` 与 `ahamark-business-e2e-4a09-20260803` 运行状态未被修改。临时 compose 位于 `C:\Users\Lenovo\AppData\Local\Temp\ahamark-5c49-migration-20260804-05\docker-compose.yml`。

Git 守卫：最终提交前审查起点为 detached `2b24ab4001e9f36b0ea32337331a60f53c3d33c1`；`ahamark.db`、根 `package-lock.json`、`apps/web/package-lock.json` 与 `0031_student_portal.py` 均无真实 diff。`0031` 工作树与 HEAD blob 均为 `517d2f0f42b1a4c9d18b4ce0f401aaa8b5044426`，Windows 行尾/stat 会使本地 `git status` 显示伪 `M`，不得暂存。用户已于 2026-08-04 授权在最终只读审查通过后，以明确文件 allowlist 暂存、提交并安全推送到现有 Draft PR #1 分支；仍禁止合并、部署或把 PR 转为 ready。

提交前结论：本专项最终只读审查已通过，没有 P0/P1、安全边界、迁移准确性或范围阻塞；授权范围仅包含把本专项提交并推送到现有 Draft PR #1，提交与远端结果以 Git/PR 当前 head 为准，不包含合并或部署。

remaining risks / 下一位接手：如需把验收结论提升为“同一个标准参考答案+评分标准 PDF 从上传直至发布完整通过”，必须配置受控的非 Fake 内容 Provider 或扩展明确标记的合成 fixture，使参考答案来源可确定且不冒充真实 Provider；还应将 `business_browser_e2e.mjs` 的历史“先 OCR 手工建题、再一次生成”顺序整体迁移到新版“一次生成优先”流程，避免旧脚本人为生成重复题。不要修改 `ahamark.db`，不要删除上述证据卷；启动本轮项目后仍须只用对应 project 名操作。

下一位接手事项：先完整阅读本节，再从 Draft PR #1 当前 head 审查本专项提交，特别确认授权例外只修改 `0007`、`0031` 无真实 diff、新迁移测试的数据库保护条件充分。`npm install` 按现有锁文件安装测试依赖时报告 4 个 high severity advisories，本轮未执行会改动依赖的 `npm audit fix`；后续应另开依赖安全专项评估。不得修改 `ahamark.db`，不得删除上述证据卷。

> **当前仓库状态（2026-07-28）：** 本地 `master` 功能基线位于
> `2377cd3`（包含线性代数批改第 1–4 部分），尚未 push；Alembic
> 唯一 head 为 `0025_ai_grading_audit_contract`。第 5 部分离线评测命令见
> `scripts/linear_algebra_offline_evaluate.py`；所有样本均为本地 Codex 生成的合成数据。
> Assignment Generation（编排、元数据/文件分析、题目提取、答案与 Rubric 草稿、集中复核发布、
> Provider 调用审计）已按受控、仅建议方式落地。Provider 默认 `unavailable`，外部请求默认
> `false`，`suggestion-only=true`；AI 不能自动发布作业，也不能写入最终成绩。
> **REAL-PROVIDER QUALITY PENDING**。本地开发阶段由 Codex 代为执行需要 API 的草稿生成，
> 结果仍需教师确认。合入 `master` 不代表已部署，
> 本次合并也没有自动执行任何数据库迁移。本项目仍不代表 Production Ready。

> 原定第一至第八部分均已正式关闭，并已形成连续、可追溯的八提交链。第八部分功能基线为
> `cc9146a5edf001817915c020f7aa26bc8053b989`；本地预生产门禁 8A–8E 及 Edge 已 PASS，正式 Run 为
> `v8-final-20260725-c6568104`，证据入口见
> [`docs/PREPRODUCTION-READINESS.md`](docs/PREPRODUCTION-READINESS.md)。该门禁只证明本地 API
> 层故障切换，不建立生产高可用或灾备，项目等级仍为 C。
>
> 批改闭环最终集成基线包含 `4c6266b` 与 `8746e18`：Structured Rubric 使用题目真实满分，
> `manual_only` 可绑定空 `validation_rule`，集中审查过滤 stale/superseded 并限制人工解决动作，
> 浏览器门禁有界；failed ReportJob 只能创建新任务重试，XLSX 所有外部文本列均防公式注入。

AhaMark 是面向教师的 AI 作业批改与学情分析平台。当前已实现数据库会话认证、Submission OCR 工程链路、教师评分复核、不可变成绩发布、异步 Excel/中文 PDF 报告和版本化学情统计。RapidOCR 是真实本地印刷体 OCR；当前没有真实主观题 AI Provider，主观题必须人工评分。第五部分权限与文件安全、第六部分开发机有界容量及第七部分开发环境备份/故障恢复均已完成定义范围内验收。整体等级仍为 **C（内部演示或开发测试）**，不适合真实学生数据、真实教学试点、生产部署或公网开放。

## 当前可用的教师流程

- 作业创建向导支持试卷拖拽/点击上传、文件状态展示、页面缩略图预览和当前页切换。
- 截止时间支持“无截止时间”或手动设置日期与时间。
- 已发布作业详情页提供“上传学生作业”入口；教师可创建批改批次并上传 PDF/PNG/JPG/JPEG。
- 学生作业文件会按文件名中的学号或班级内唯一姓名自动匹配，歧义匹配需教师确认。
- 集中审查将问题翻译为教师可理解的说明，已解决问题默认收起，未解决阻塞项优先展示。
- AI Provider 当前默认不可用；由 Codex 代跑草稿生成时，结果仍作为待教师确认的建议，不自动发布或写入最终成绩。

## 本地运行与数据位置

推荐在 D 盘工作区运行：

```powershell
cd D:\OpenAIData\Workspaces\AhaMark
Copy-Item .env.example .env
docker compose up --build -d
Invoke-WebRequest -UseBasicParsing http://localhost:8000/health
```

Web 地址为 <http://localhost:3000>，API 健康检查为 <http://localhost:8000/health>。
Docker 数据、项目工作区和桌面交付文件已迁移到 `D:\OpenAIData`；C 盘保留的路径只是兼容性目录联接。
不要提交 `.env`、数据库文件、`node_modules` 或 `.next`。

## 第七部分：开发环境恢复验收

第七部分 7A–7D 为 PASS：

- PostgreSQL 独立逻辑备份恢复：开发环境 PASS
- MinIO 独立对象恢复、metadata、引用、文件解析及孤儿对账：开发环境 PASS
- 单 API/单 Worker 的 Worker、Redis、MinIO 故障恢复：开发环境 PASS
- 运维文档、脱敏摘要和证据收口：PASS

正式证据入口：

- [备份恢复手册](docs/BACKUP-RESTORE.md)
- [故障恢复手册](docs/FAILURE-RECOVERY.md)
- [备份恢复摘要](docs/backup-restore-verification.json)
- [故障恢复摘要](docs/failure-recovery-verification.json)

本结论不建立生产灾备、生产高可用、生产 RPO/RTO、SLA 或多实例恢复能力。异地、加密、
增量和长期备份均未验证。观察 RPO 为 0 秒仅因备份窗口无源写入；2.314 秒仅是独立数据库
恢复耗时。Broker visibility timeout 为 15 秒，正式重投完成观察值为 102.230 秒。

第一部分基线入口：`docs/PROJECT-BASELINE.md`。能力证据、数据安全边界和统一产品措辞分别见 `docs/CAPABILITY-EVIDENCE-MATRIX.md`、`docs/DATA-SECURITY-BOUNDARIES.md`、`docs/PRODUCT-CAPABILITY-STATEMENTS.md`。这些文档严格区分实现、自动化验证、真实环境证据和生产可用性。

## 教师认证与安全边界

正式会话认证位于 `app/api/auth.py`：密码使用标准库 scrypt（独立随机盐），登录创建随机数据库会话，浏览器只保存 HttpOnly `ahamark_session` Cookie；另有 SameSite=Lax CSRF Cookie，带会话的写请求必须发送 `X-CSRF-Token`。会话默认 12 小时，支持撤销、过期、当前用户与退出。production 登录限速使用 Redis 共享固定窗口状态，已验证双 API 实例累计失败次数；默认窗口 300 秒、阈值 5 次，Redis 不可用时 fail closed。限速 key 使用 HMAC，不包含明文密码。生产环境 Cookie 自动 Secure，且 `APP_ENV=production` 时绝不回退到 demo actor。

当前未开放公共注册。管理员在受控环境中执行 `python -m app.cli.create_teacher --email teacher@example.com --display-name 教师姓名`，按不回显提示输入密码，然后访问 `/login`。前端使用 Cookie，不把长期令牌写入 localStorage；教师布局通过 `/auth/me` 保护。开发期 demo actor 只有 `DEMO_ACTOR_ENABLED=true` 且非 production 时可用。共享限速仅在本地预生产式双 API 环境验证，不代表公网 DDoS、WAF 或生产攻击面能力。

## 线性代数主观题批改（第 1–5 部分）

当前闭环是“本地 Codex/离线建议 → 数学验证与证据约束 → 教师复核 → TeacherReview/最终成绩”，
AI 不自动定分、不创建 GradeRelease。题型 registry、严格引用/版本护栏、Worker 审计闭环和教师复核
页面已实现；默认 Provider 仍为 `unavailable`，测试 Fake 仅限 `APP_ENV=test`，不代表真实 Provider。

离线评测只使用合成数据，不上传文件、不调用外部 API：

```powershell
$env:PYTHONPATH='apps/api;.'
python scripts/linear_algebra_offline_evaluate.py data/linear_algebra_evaluation_v1.json `
  --output docs/linear-algebra-evaluation-v1-report.json
```

评测集覆盖 24 例和全部 registry 题型；当前报告要求 `false_verified=0`、引用拦截率与
manual/unsupported 遵从率 100%、状态准确率至少 95%，并保留人工复核证据、隐私、成本和延迟
作为未来真实 Provider 的前置门槛。当前安全模式 gate 通过不等于生产可用或真实质量验证。

## 最终成绩、发布与分析

唯一最终成绩入口是 `FinalScoreService`：Submission 必须属于当前教师且为 `finalized`，每个 Submission 只取版本最高的 `SubmissionScoreSnapshot.status=complete`，并严格校验 details。绝不回退到 GradingResult、AI 建议、临时 TeacherReview、incomplete 或 superseded 数据。没有快照是“未完成”，不是零分。

新版 details schema 包含题目 ID/题号/题型、最终分/满分、TeacherReview ID、最终错误代码/评语、知识点 ID、评分方法和确认时间；校验题目不重复、分值范围、题目存在、顶层分数与分题和一致。旧快照缺少必填字段时不会进入发布或统计。

`GradeRelease` 以作业/班级递增 version 保存发布记录，`GradeReleaseItem` 固定具体 ScoreSnapshot ID。released 的产品含义仅是“教师已确认发布数据，尚未发送到学生端”，不是学生已收到。修改单个学生后，`confirm-results` 会列出学生与变化题目，只生成该学生的新快照并复用其他学生的旧快照；新发布版本保留完整历史。旧 `POST /api/grade-releases` 已标记 deprecated 并稳定返回 `410 GRADE_RELEASE_CREATION_RETIRED`，每次调用会记录客户端来源和替代入口，不产生正式成绩写入。

主观题建议按 Rubric 分项保存简短理由和答案区域依据；零分、满分及置信度临界结果会自动二次复核。同题同版本的相同答案若出现分数或分项差异，只进入教师“需检查”队列，不会自动改分或形成正式成绩。

Excel 是真实 `.xlsx`，包含“成绩总表、题目统计、知识点统计、导出说明”。学号强制文本，缺失成绩不写零，外部文本防公式注入。API 只创建 ReportJob 并派发 job ID；`workers/tasks/reports.py` 幂等生成、写对象存储、登记 StoredFile。该边界已有自动化测试及真实 Celery/MinIO 冒烟。个人与批量学生 PDF 使用仓库内 Noto Sans SC TTF，来源、许可证和校验值见 `apps/api/assets/fonts/SOURCE.md`。

AnalyticsSnapshot 固定 GradeRelease，旧快照不覆盖。后端统一计算参与人数、平均/最高/最低/中位数、归一化分数段、题目得分率/满分率/零分率、客观题正确率、知识点掌握率、教师确认错误频次和透明 A/B/C/D 临时分层。未完成学生不进入分母；一题多知识点时完整计入每个知识点并明确样本。主观题不显示“正确率”。RuleBased 教学建议只引用快照 metrics 的题目 ID、得分率和样本数；没有真实 AI 教学助手。

主要 API：

- `GET /api/assignments/{assignment}/classes/{class}/grade-readiness`
- `POST /api/grading-batches/{batch}/confirm-results`（唯一正式成绩创建入口）
- `GET /api/grade-releases`、`GET /api/grade-releases/{id}`、`POST .../cancel`
- `POST /api/grade-releases/{id}/reports`、`GET /api/report-jobs/{id}`、`GET .../download`
- `POST /api/grade-releases/{id}/analytics`
- `POST /api/analytics/{id}/insights`

当前仓库 Alembic 唯一 head 为 `0024_nullable_publish_readiness_due_at`；`0010_report_student`
是下述报告与学情功能对应的历史迁移节点。`/analytics` 已包含加载、空、错误、小样本、0–100%
图表、键盘可访问表格和数据版本选择。分数段、题目、知识点、最终错误类型均可分页下钻；班级、
学生及知识点历史趋势只读取每份作业最新有效发布版本，缺失作业不记零。学生详情路由为
`/analytics/students/{studentId}`，展示发布成绩、各题最终值、知识点、教师确认评语、
ScoreRevision 与真实 ReportJob 状态。

Analytics 7.1 新增 API：

- `GET /api/analytics/{snapshot}/score-bands/{band}/students`
- `GET /api/analytics/{snapshot}/questions/{question}/students`
- `GET /api/analytics/{snapshot}/knowledge-points/{knowledge_point}`
- `GET /api/analytics/{snapshot}/errors/{error_type}`
- `GET /api/classes/{class}/analytics/trends`
- `GET /api/students/{student}/analytics/trends`、`GET /api/students/{student}/analytics`
- `GET /api/classes/{class}/knowledge-points/{knowledge_point}/trend`
- `GET /api/students/{student}/knowledge-points/{knowledge_point}/trend`
- `GET /api/students/{student}/report-jobs`
- `GET/PATCH /api/teaching-insights/{insight}`、`POST .../confirm`、`POST .../regenerate`、`POST .../invalidate`

所有下钻使用 CurrentUser，并校验 AnalyticsSnapshot、Assignment、Class 与固定 GradeRelease 的所有权；列表默认 20 条、最多 100 条并稳定排序。教学建议明确标记为“规则型教学建议”，保留原始内容及编辑历史，确认后不可静默修改，evidence 数字在确认时与固定 AnalyticsSnapshot 再校验。前端未引入图表库，使用原生 HTML/CSS，因此无新增许可证和锁文件变化。

历史上的后续接手条件包含浏览器闭环、性能、安全专项、代理和隔离矩阵；第五部分权限与
文件安全、第六部分开发机容量和第七部分开发环境恢复现已完成定义范围内验收。生产容量、
生产灾备、高可用、正式部署和运维体系仍未建立。当前仍没有真实主观题 AI Provider，
主观题必须教师人工评分。

## 验收与交付入口

- 最终验收：`docs/FINAL-ACCEPTANCE.md`
- 安全与文件策略：`docs/SECURITY-AUDIT.md`、`docs/FILE-SECURITY.md`
- 性能结果：`docs/PERFORMANCE.md`、`docs/performance-results.json`
- 部署、代理、备份恢复和排障：`docs/OPERATIONS.md`
- 最终交接：`docs/HANDOFF.md`

本地生产样式代理（不含 TLS）使用：

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up --build -d
Invoke-WebRequest -UseBasicParsing http://localhost:8080/health
```

50 人合成数据可重复初始化与安全清理：

```powershell
docker compose exec -T api python -m app.cli.seed_performance_demo
python scripts/performance_smoke.py
docker compose exec -T api python -m app.cli.cleanup_performance_demo --confirm-marker performance50.synthetic.invalid
```

清理命令只接受固定 marker，并在事务中校验固定教师 ID/邮箱、打印范围；不会删除结构、Bucket、未知对象或 Docker Volume。

### Analytics 7.2 真实验证与 UI

非破坏性更新测试栈（保留 PostgreSQL、Redis、MinIO 命名卷）：

```powershell
docker compose up --build -d api worker web
docker compose exec -T api alembic upgrade head
docker compose exec -T api python -m app.cli.seed_analytics_demo
python scripts/verify_analytics_http.py docs/analytics72-http-verification.json
node scripts/analytics_browser_smoke.mjs
```

`seed_analytics_demo` 使用固定 UUID 和 `analytics72.synthetic.invalid` 标记，幂等创建两名合成教师、两个隔离班级、三名主场景学生、三份不同满分作业、三次 GradeRelease、一次缺交、两个 KnowledgePoint、两种最终错误类型、ScoreRevision、completed/failed ReportJob 和规则型 TeachingInsight。数据均为合成值；重复执行不会重复插入。验证完成后，只能用明确标记清理：

```powershell
docker compose exec -T api python -m app.cli.cleanup_analytics_demo --confirm-marker analytics72.synthetic.invalid
```

清理命令先验证固定教师 ID 与合成邮箱，只删除这两个 owner 的数据；不要用 `docker compose down -v`。真实 HTTP 脚本使用 Cookie+CSRF，验证四类分页下钻、稳定排序、三类趋势、学生详情、ScoreRevision、报告重新生成、TeachingInsight 生命周期、404/422 和 Teacher B 隔离，并将无密码、Cookie 或 CSRF 的结果写入 `docs/analytics72-http-verification.json`。

Analytics UI 现提供规则建议查看、evidence、编辑、草稿、确认、重新生成、失效、状态、loading/disabled 和成功/错误提示；明确标记为规则型建议。学生详情提供 0–100% 学生得分率折线图、按 KnowledgePoint ID 的掌握率折线图及等价表格。failed、expired、partially_completed 报告按钮调用 `POST /api/report-jobs/{id}/retry` 创建新 ReportJob；不是恢复原任务。completed 报告每次重新请求短期签名 URL。

Analytics 范围的无头 Edge 冒烟覆盖 Teacher A 登录、选择真实发布、分数段下钻、
Insight 编辑确认、学生和知识点趋势，以及 Teacher B 学生详情拒绝。完整业务浏览器
E2E、第五部分安全专项和第六部分开发机有界容量现已完成。第六部分在单 API/单 Worker
合成环境覆盖 50 名不同学生报告、Fake/RapidOCR 至 250 页及 200 人/100 题 Analytics；
最大规模 Analytics 学生读取约 8 秒。生产容量、SLA、多实例扩展、故障恢复和生产部署
验收仍属于后续范围。

## 学生作业与批改流程

教师为已发布且 PaperVersion/RubricVersion 完整的作业创建 GradingBatch，上传 PDF/PNG/JPG/JPEG。后端使用随机对象键保存文件，学号精确匹配优先，其次是班级内唯一姓名；重名、多个标识或无标识只生成待确认记录。一个学生的多张图片或 PDF 页面按文件顺序归并为同一 Submission，原文件和 SubmissionPage 均保留，不静默覆盖重复校验值。

Submission OCR 数据与试卷 RecognitionJob/PaperPage 隔离：学生域使用 SubmissionRecognitionJob、SubmissionPage、StudentAnswer 和 StudentAnswerRegion，坐标仍为未旋转原始页左上角 0–1。`recognized_*` 永久保留原始值，`corrected_*` 有值时评分优先读取修正值。空白、低置信、公式不可用和失败是不同状态。现有 RapidOCR 转换/预处理组件可复用于学生页；第六部分已完成 Fake OCR 的 Celery/MinIO 150/200/250 页编排和独立 RapidOCR 清晰印刷体 100/150/250 页吞吐阶梯，但二者不能互相替代，也不证明真实学生答卷准确率、手写或公式能力。

客观题 `single_choice`、`multiple_choice`、`true_false`、`fill_blank` 采用大小写与空格规范化后的确定性精确匹配，使用标准答案及可接受答案。单位/精度等无法由明确规则判断时应进入人工复核。主观题使用统一 GradingProvider；默认 UnavailableProvider 返回 `score=null`。FakeGradingProvider 只允许非 production 自动化测试，production 配置 fake 会安全降级为 unavailable，绝不能作为真实 AI 成绩。

教师复核支持接受、修改、拒绝、手动评分和需要更多信息。修正答案会使旧建议 superseded；每次最终分数/评语变化写 ScoreRevision。低置信、OCR/公式异常、`score=null`、Provider unavailable、修正答案和 Rubric 版本变化均不能直接成为最终成绩。当前 API 未开放“一键无条件接受”，批量接受必须在后续 UI 完善时复用同一后端资格规则。

`POST /api/submissions/{id}/finalize` 会逐题检查答案、教师最终分、分值范围、强制复核和当前 RubricVersion，并生成新的 SubmissionScoreSnapshot 版本而不覆盖旧版本。第七部分只能读取最新 `status=complete` 快照；`details` 保存每题 question/answer/review ID、最终分、满分、错误类型和评语。AI/规则 GradingResult 不是最终成绩来源。

学生作业与批改子系统对应迁移为 `0006_submissions_grading_review`；仓库当前 Alembic 唯一 head
为 `0023_assignment_provider_invocation_audit`：

```powershell
python -m alembic upgrade head
python -m alembic upgrade head --sql
python -m pytest -q
npm.cmd run format
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
```

## 技术栈与本地运行

- Web：Next.js 15、React 19、TypeScript、Tailwind CSS 4。
- API：FastAPI、SQLAlchemy 2、Alembic；生产数据库为 PostgreSQL。
- Worker：Celery + Redis；对象存储：MinIO。
- 文字 OCR：RapidOCR 3.9.2 + ONNX Runtime 1.27.0（本地处理，不上传第三方）。

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ocr]"
npm.cmd install
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir apps/api --reload
.\.venv\Scripts\python.exe -m celery -A workers.celery_app:celery_app worker --loglevel=INFO
npm.cmd run dev
```

复制 `.env.example` 为 `.env`、替换专用凭据后运行 `docker compose up --build -d`。Compose 定义 PostgreSQL 16、Redis 7、MinIO、API、Worker 和 Web；2026-07-22 已完成空库迁移、六服务启动、Celery 往返和 MinIO 上传/读取/签名 URL 冒烟。

## 分值完整性规则

迁移 `0005_nullable_question_score` 允许草稿/OCR 题目的 `Question.max_score` 为 `null`。未知分值不会写入 0、1 或其他哨兵值：候选可保留 `suggested_score=null`，确认后成为“分值未设置”的待完善题目。此类题目不能保存 Rubric、不能发布；发布检查返回题目 ID、题号和步骤：`QUESTION_SCORE_REQUIRED` 与 `ASSIGNMENT_TOTAL_SCORE_INCOMPLETE`。总分只汇总已知值，同时报告完整性错误。手工题目输入仍要求正数。

## OCR Provider 与边界

配置 `RECOGNITION_PROVIDER`：

- `rapidocr`：真实本地印刷体文字 OCR。安装 `pip install -e ".[ocr]"`；随 RapidOCR 包使用 `PP-OCRv6_det_small.onnx`、`ch_ppocr_mobile_v2.0_cls_mobile.onnx`、`PP-OCRv6_rec_small.onnx`。输出文字、0–1 页面坐标、0–1 置信度、provider/version/source/status；不生成 LaTeX。
- `fake`：只允许非生产自动化测试。`APP_ENV=production` 时选择 fake 会降级为 unavailable，不能用它评估准确率或宣称真实 OCR 可用。
- `unavailable`（默认）：明确禁用识别，但转换/预处理仍可用。

真实最小验证使用运行时合成、无个人信息的小图：清晰中文印刷体、中英数字、空白、低对比度和损坏字节；验证了文本、坐标、置信度、空结果、错误映射和 RecognitionBlock 持久化。样本极小，未计算 CER/WER，不代表真实教学、手写、公式、表格或几何能力。公式 provider 独立为 unavailable；普通数学字符只保留为 text 并进入人工复核。DOCX 仍因缺少 LibreOffice headless 返回 `DOCX_CONVERTER_UNAVAILABLE`。

## 文件与异步链路

API 创建 RecognitionJob 后只向 Celery 发送 job ID；派发失败会把数据库任务标为 `failed/WORKER_UNAVAILABLE`。Worker 从数据库和对象存储重新读取输入，写入 rendered/processed/thumbnail 键及页面、Block、Candidate。任务和页面重试复用数据库页面记录；状态以数据库为用户可见真相。

MinIO 原始键位于 `assignments/...`，衍生键位于 `recognition/{owner}/{job}/{page}/{kind}-{uuid}.png`，API 逐级校验 owner 后返回短期签名 URL。当前实现只按明确对象键操作，没有宽泛孤儿清理。2026-07-22 已在专用 Docker 测试栈完成 PostgreSQL 在线迁移与回滚再升级、Redis/Celery 消费、MinIO 上传/读取和签名 URL 生成；这是开发环境连通性证据，不是生产容量或安全证明。

## 健康与验证

`/health` 保持轻量；`/ready` 在短超时内分别报告 `postgresql`、`redis`、`celery_worker`、`minio`、`text_ocr`、`formula_ocr` 的 available/unavailable/degraded 状态，不返回凭据。FakeProvider 只会让文字 OCR 显示 degraded。

PostgreSQL 专用测试库示例（执行 downgrade 前必须再次确认目标不是生产库）：

```powershell
$env:DATABASE_URL='postgresql+psycopg://ahamark:<password>@localhost:5432/ahamark_55_migration_test'
python -m alembic upgrade head
python -m alembic current
python -m alembic downgrade 0004_recognition_pipeline
python -m alembic upgrade head
```

离线 DDL 与完整质量命令：

```powershell
$env:DATABASE_URL='postgresql+psycopg://ahamark:integration-only@127.0.0.1:5432/ahamark_55_migration_test'
python -m alembic upgrade head --sql
python -m alembic downgrade 0005_nullable_question_score:0004_recognition_pipeline --sql
python -m ruff format --check apps/api workers tests
python -m ruff check apps/api workers tests
python -m mypy
python -m pytest -q
npm.cmd run format
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
```

历史验证记录详见 `docs/HANDOFF.md`。第七部分关闭轮当时复用刚完成的后端门禁：
113 passed、2 skipped，Ruff format/check 113 files，mypy 52 files；7D 另执行 JSON、原始
证据哈希、Markdown UTF-8、相对链接、陈旧口径、敏感字段和 Git diff 门禁。该轮结束时
第七部分工作树仍未暂存、未提交、未推送或部署；随后第一至第八部分均已提交。
