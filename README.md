# AhaMark

2026-08-07 累计试用修复与题目页面切题选择性移植（完成，已提交并推送 `codex/integrate-question-page-cutter`）：从 GitHub `gyh--001` 的 `62024c0` 仅吸收旋转感知页面预览、图片拖框和题目+区域原子保存的有效设计，没有合并其三步向导、Legacy Rubric 路径或对现有生成/集中审查流程的删除。当前仍是 Structured-only 六步教师流程；“手动框选题目”默认关闭，只有教师点击“开始手动切题”才生成当前旋转方向的 PNG 预览并接受一次拖框，保存成功后自动关闭，切页/旋转/退出会丢弃未保存框并拒绝过期预览。教师可原子新建题目+首个区域，也可把第二页区域追加到已有题目；服务端在 assignment 行锁内只允许 draft + active paper + ready page + active question，双目标/错页/越界均 422，重复题号和 90% 高重叠区域分别 409，发布后预览或修改同样 409。保留原有数值区域编辑作为后备，不自动生成、确认或发布正式题目、答案、评分标准或成绩。

同一提交同时收口本轮试用已记录的批改页面过亮误报、PDF 题号锚点自动切题与幂等重跑、手动区域原子替换、历史 removed/旧 paper 答案隔离、blocker 去重与明确文案、题序稳定排序、Codex applied evidence 只接受 confirmed current region，以及答案识别校对截图使用 processed evidence 的方向修复；新增迁移保持 `0035_question_anchor_segmentation → 0034_structured_rubric_authority` 单线，不修改任何旧迁移。最终后端全量单次为 `646 passed, 18 skipped`，数据库守卫明确 `ahamark.db unchanged`；前端全量 `28 files / 173 tests passed`，Prettier、ESLint、TypeScript、Next production build（19 pages）、全仓 Ruff、strict mypy `109 source files`、Alembic single head、`git diff --check` 均通过。Next build 仍有既有可选 SWC lockfile patch 与 Windows standalone symlink 警告但退出码为 0；根 lockfile SHA-256 仍为 `8FAC7F389094E4978DDA7C04200D325C031565B3FFDC581ADA3295808C18EDFF`，Web lockfile、`ahamark.db` 与临时根 `node_modules` 均不存在。Docker context 为 `desktop-linux`；隔离 `ahamark-assistant6-preview-20260806` 六服务在最终只读审计时 healthy，`ahamark-user-test-ac7ceb6` 六容器 ID/Exited 状态及未知 volumes 未被修改或删除。本次没有为新手动切题功能重跑 fresh browser E2E，因此不能把 Vitest/production build 夸大为真实浏览器端到端证据；既有浏览器证据仍只使用 Fake/Codex local synthetic 数据，不代表真实 Provider 输出质量。GitHub 最终提交前复核仅有已审计的 `gyh--001` 新分支；Draft PR #1 仍 OPEN/Draft/CLEAN/MERGEABLE、head `8a76715`、无评论/审查/checks，本次不改变 PR #1、不合并、不部署。

2026-08-06 答案识别校对截图方向修复（完成、未暂存/提交/推送）：教师反馈“答案识别与校对”的截图方向错误；当前 synthetic 页实际 `rotation=90`，5 个识别块的 `region_evidence_image_id` 均正确关联 `source_kind=processed` 的旋转后证据，但冗余 `evidence_image_key` 仍指向 original 截图，前端因此用处理图坐标覆盖未旋转原图。现识别块 API 以关联的 `RegionEvidenceImage` 为显示权威，只有 source 为 processed 且 page/region 均与 block 一致才返回图片，关联异常时 fail closed 不返回错图；新建识别块的冗余 key 也统一写 processed key。该兼容路径使已有批次无需重跑识别即可修复方向。答案识别完整文件 `20 passed`，Ruff lint 与两个产品文件 strict mypy 通过；新版 Ruff format-check 仍会要求重排三个已有历史大文件及统一其 CRLF，未接受该无关整文件 diff。隔离 API/worker 已重建，六服务 healthy，复核页 HTTP 200；线上只读回读为 current blocks 5、processed keys 5、missing URLs 0。未重新识别、修改答案文字、确认结果、写最终成绩或发布成绩。

2026-08-06 结果确认未完成项明确显示（完成、未暂存/提交/推送）：当前 synthetic 批次的 readiness 实际返回 15 个阻塞项，即题号 1–5 各有 `CONFIDENCE_LOW`、`REQUIRES_REVIEW`、`CRITERION_INCOMPLETE`；前端缺少这三个错误码的文案映射，全部回退成同一句“请检查未完成项”，再经去重后只剩一条，导致教师无法判断待办。现补充明确中文说明：“评分建议置信度不足，需要教师逐题核对”“评分建议仍在待复核状态”“评分项尚未完成教师确认”，保持既有 fail-closed 门禁，不自动复核、确认或发布成绩。新增三类 blocker 不再落入通用回退的回归；批改复核页完整文件 `25 passed`，TypeScript、ESLint、两文件 Prettier 与 Next production build（19 pages）通过，`git diff --check` 通过。验证期间仅临时只读复用旧 worktree 依赖，Junction 已精确移除；未改动旧目录、依赖或 lockfile。隔离 Web 已重建，六服务 healthy，当前复核页 HTTP 200；未自动复核、确认或发布成绩。

2026-08-06 批改复核题序稳定排序（完成、未暂存/提交/推送）：教师观察复核页题目未按题序排列；当前 API 实际返回首题为第 4 题，根因是 review-workspace 虽已严格筛选 active paper + active questions，但 StudentAnswer 查询没有 `ORDER BY`，PostgreSQL 因此按无保证的物理/UUID 顺序返回，前端导航与上一题/下一题逻辑忠实沿用该乱序。现后端统一按 `Question.display_order → Question.question_number → StudentAnswer.id` 稳定排序，所有复核页/协作过滤消费者获得同一顺序；不依赖题号字符串的字典序作为主排序，也不改变题目内容、答案、分数或复核状态。新增反向插入第 2 题后第 1 题仍返回 `[1,2]` 的回归；submission workflow 完整文件 `26 passed`，Ruff 与产品文件 strict mypy 通过。隔离 API 已重建，review-workspace 回读当前 synthetic 题序精确为 `1,2,3,4,5`，复核进度仍为 0/5，复核页与 API 均 HTTP 200；未自动复核、确认或发布成绩。

2026-08-06 Codex apply 历史同坐标证据误冲突修复与当前批次处理（完成、未暂存/提交/推送）：教师要求继续处理当前 synthetic 批次；processing run `eeece75d-a07f-4bc8-b4c0-5ddb06ba326e` generation 3 正确只纳入 5 个 active answers、0 blocker，并产生 5 个 Codex local suggestion-only work items。5 项均 submit/apply 后，首次 reconcile 仅第 4 题 succeeded，其余 4 项被 fail closed 标为 `CODEX_APPLY_CONTRACT_CONFLICT / Evidence children do not match response`。只读核对确认建议 payload 与当前 region 均有效；真正原因是 applied child 审计按坐标反查 GradingEvidence 时未限定 region status，自动切题代际保留的 superseded 历史区域与当前 confirmed 区域同坐标，因此 actual evidence 集合被旧 ID 污染；第 4 题手动重框坐标不同所以未触发。现证据反查只接受 confirmed region，仍严格要求 answer/page/坐标/数量与响应引用完全一致，不接受 candidate、stale、superseded 或 rejected。新增同坐标 superseded 回归，Codex apply 完整文件 `10 passed`，Ruff 与产品文件 strict mypy 通过。隔离 API 已重建；修复后对同一 generation 3 幂等 reconcile，无需重建或删除 work item，最终状态 `awaiting_teacher_review`、5/5 succeeded、0 failed、0 pending Codex、无 error。所有结果仍为 synthetic Codex-assisted suggestions，必须由教师复核；未自动确认答案或成绩、未创建正式成绩快照、未发布或释放成绩。

2026-08-06 当前识别仅校验 active answers（完成、未暂存/提交/推送）：批改页报 `all answers require a confirmed region`；只读核对确认当前 active 题号 1–5 均已有且仅有 1 个 confirmed region，真正阻塞者是同一 submission 内保留审计的 `removed` 历史第 3 题答案。此前仅在 processing orchestrator/review-workspace 隔离历史答案，识别启动 API、实际 OCR worker 与 recognition 自动确认仍查询全部 StudentAnswer。现三层统一限定为 assignment 的 `active_paper_version_id` 且 `QuestionStatus.active`，区域输入 hash 和证据生成同样只包含该集合；removed/旧 paper 答案继续保留但不再要求区域、不生成 OCR 证据、不阻塞当前识别。新增 worker 与自动确认回归；首轮 33 项聚焦测试准确暴露 API 门禁遗漏（`1 failed, 32 passed`），补齐后最终 `33 passed`；Ruff 与三个产品文件 strict mypy 通过。隔离 API/worker 已重建，六服务 healthy；使用 synthetic 教师和 Fake provider 重跑任务 `d8237178-982e-4bed-89db-af88e2d08de0` 已 completed、无 warning/error，数据库回读为 active 题号 1–5 各 1 个 confirmed region/1 条本任务 evidence，removed 历史答案为 0/0。该证据不代表真实 Provider 输出质量；未使用真实学生数据、未确认正式内容、未写最终成绩、未发布或释放成绩，未触碰旧用户测试栈。

2026-08-06 手动框重复与提交级 blocker 去重（完成、未暂存/提交/推送）：教师看到 5 条 `Every answer must have exactly one current region`；只读实证并非五题均异常，而是第 1/2/3/5 题各 1 个 confirmed region，第 4 题因连续手动框选留下 2 个 `confirmed/manual/TEACHER_DRAWN/teacher_explicit`，同一个提交级 `SEGMENTATION_AMBIGUOUS` 又被复制到 5 个 answer-scoped processing steps。现将 POST 手动区域改为原子替换：跨题高重叠仍先 fail closed，同题现有 candidate/confirmed/manual_required 仅在新框可成功创建的同一事务内转为 `superseded` 并递增 version，审计记录被替换 ID；不会删除历史。自动确认门禁也对既有多 current 且含 teacher_explicit 的数据执行确定性教师优先：以 `created_at,id` 最新一次教师框为唯一 winner，其余转 superseded，并写 `processing.segmentation.teacher_region_precedence` 审计；没有教师显式框的歧义仍继续 fail closed。批改页按 submission + error code + message 去重 blocker，因此同一答卷同一问题只显示一次，不合并不同 submission。后端两个完整文件 `26 passed`，批改页完整文件 `12 passed`；Ruff、两个产品文件 strict mypy、Next production build（19 pages）通过。隔离 API/worker/Web 已重建；当前 synthetic 第 4 题已精确收敛，winner `02d23745-c8f4-44f0-9ef8-ed99ee8c99b2`，旧框 `8323201b-7f7f-46f8-83c0-0633129aa395` 保留为 superseded，最终题号 1–5 均恰好 1 个 current region，审计回读一致。未确认正式内容、未写最终成绩、未发布或释放成绩，未触碰旧用户测试栈。

2026-08-06 手动框选显式启动门禁（完成、未暂存/提交/推送）：按教师要求，进入切题调整态或出现未完成题时不再自动启用图片拖框。新增独立“开始框选/退出框选”按钮，默认 `data-draw-enabled=false` 且普通拖动不会调用新增区域接口；启动后画布显示十字光标与明确提示，只允许当前所选题目/页面的一次拖框，成功保存为 `teacher_explicit` 后自动关闭。切换题目、切换页面或进入/退出调整态会清除未完成草稿并关闭框选，避免误拖和跨题落框；查看图片、缩放、旋转、确认和删除既有区域不受影响。组件聚焦测试 `4 passed`，其中覆盖未启动拖动零 mutation、启动后才写区域、完成态默认关闭及多页/并发 reload；ESLint、TypeScript 与 Next production build（19 pages）通过。隔离预览 Web 已重建，当前批改页 HTTP 200、六服务 healthy。未确认正式题目/答案/评分标准、未写或释放成绩。

2026-08-06 active paper question 历史答案隔离（完成、未暂存/提交/推送）：当前 synthetic 批次报错 `Answer does not use the active paper question`，只读核对确认同一 submission 有 6 条 StudentAnswer：5 条指向 active paper 的 active questions，另 1 条指向同一 paper 内已 `removed` 的历史第 3 题；后端 input snapshot 的 fail-closed 拒绝正确，但处理 manifest、识别输入、识别完成后的 Codex suggestion 展开和 review-workspace 仍把历史答案纳入当前队列。现统一只选择 assignment.active_paper_version_id 下 `QuestionStatus.active` 的答案；confirmed regions 也按该 active answer 集合收窄，removed/旧 paper 答案保留数据库审计但不再创建处理步骤、阻塞当前 run 或显示给教师。active answer 的 question version、Structured Set、证据与发布门禁仍由原 snapshot 严格验证，没有放宽 `SUBMISSION_SCOPE_MISMATCH/PROCESSING_INPUT_STALE`。新增 manifest 与 review-workspace 回归；相关两文件首轮为 `28 passed, 1 failed`，唯一失败是新 review 夹具漏建 active 对照答案，补齐后两个新增回归 `2 passed`；完整 orchestrator 组首轮为 `34 passed, 13 skipped, 1 failed`，唯一失败是旧用例用随机 UUID 伪造题目，改为真实 active paper/questions 后受影响旧用例连同两个新增回归 `3 passed`。Ruff lint 与两个产品文件 strict mypy 通过；本机新 Ruff formatter 会建议重排整份历史大文件，未接受该无关机械 diff。隔离 API/worker 已重建；当前真实预览 manifest 为 5 个 active answer、无 scope-mismatch，review-workspace 为 1 submission/5 answers（题号 1–5），六服务保持隔离运行。未删除历史答案、未写正式成绩、未确认内容、未发布或释放成绩，也未触碰 `ahamark-user-test-ac7ceb6`。

2026-08-06 批改切题工作台信息降噪（完成、未暂存/提交/推送）：教师反馈批改处理页信息量过大。现将正常完成态收敛为主画布、切题完成摘要、已识别题数和单一“调整切题”入口；默认隐藏重跑/批量确认、逐题下拉框、区域卡片、原始质量数值及单页无异常时的缩略导航，原图/处理图与缩放也合并为紧凑控制。存在未完成题、候选区域、页面质量告警或教师主动进入调整模式时，仍完整显示自动切题、逐题分配、确认/删除、旋转和异常说明，不删除人工修正能力；已确认区域在调整态只显示必要状态，诊断来源/置信度/原因仅留给未解决区域。浏览器 E2E 驱动同步兼容单页无缩略导航和默认折叠区域卡片。聚焦组件测试 `4 passed`，ESLint、TypeScript（禁用增量缓存的只读检查）、三文件 Prettier、Next production build（19 pages）通过；隔离预览 Web 已重建，当前批改页 HTTP 200、六服务 healthy，既有 `ahamark-user-test-ac7ceb6` 六容器 ID/Exited 状态未变。没有改动作业内容、切题结果、真实学生数据、正式成绩、发布或成绩释放状态。

2026-08-06 自动切题重跑重复区域修复（完成、未暂存/提交/推送）：教师在首轮 5 个题号锚点区域已 `confirmed/system_auto` 后再次点击“处理并自动切题”，旧逻辑保留 confirmed 区域又创建 5 个新候选，新候选因坐标重叠被标为 `HIGH_OVERLAP_CONFLICT/manual_required`，因此前端同时显示 10 条；答案 recognition job/block 实际均为 0，重复不来自答案 OCR。现将 full segmentation 重跑改为幂等替换：仅旧 `ocr/alignment + confirmed + system_auto` 区域转为 `superseded` 历史并递增 region version，删除旧 transient candidate/manual_required 后生成并自动确认新一代；`teacher_explicit` 区域按原 ID/确认来源保留且不生成覆盖候选。当前区域 API 只返回 candidate/confirmed/manual_required，历史 superseded/rejected/stale 不再混入工作台；question-anchors API 只返回最新 processing job，仍保留最新任务的低置信拒绝锚点供审计。回归在同一答卷连续三次 `_segment`，验证 system_auto 代际替换后每题恰一 current、无 overlap conflict，随后把第 1 题改为 teacher_explicit 再重跑，确认原区域不变且两个接口均只返回最新 5 个 current 区域/锚点；两个相关完整文件仍为 `24 passed`，Ruff 与 strict mypy 通过。隔离预览最终 job `c36dae50-e953-4b98-8b8d-051d7d23df91` completed：前端区域接口 5 条，数据库为 5 confirmed/system_auto/QUESTION_ANCHOR、5 superseded 历史、current `HIGH_OVERLAP_CONFLICT=0`；最新 anchor 接口 6 条是 5 个正文题号加 1 个 `LOW_ANCHOR_CONFIDENCE` 页脚审计记录，该页脚不生成区域。未触碰旧用户测试栈、真实数据、正式内容或成绩状态。

2026-08-06 PDF 题号锚点自动切题后备路径（完成、未暂存/提交/推送）：当前合成答卷虽有清晰的“第 1 题…第 5 题”PDF 文本层，但 Fake 预览按设计不给真实 OCR blocks；同时旧切题查询误把 status=removed 的历史测试题纳入当前题目，造成重复第 3 题，所以页面无候选区域并显示“需要人工切题”。现将分区升级为 `submission-seg-v2`：文本型 PDF 优先在本地读取内嵌文本层及归一化坐标，图像/扫描 PDF 仍只走配置的真实 OCR，不把 Fake 输出冒充真实识别；仅当前 active 题目参与映射，重复题号、重复锚点、乱序、低置信和高重叠均 fail closed。新增迁移 `0035_question_anchor_segmentation`，为每个区域持久绑定 source anchor FK，并在 anchor 上固定 `source_kind/page_version`；严格自动确认 v2 只接受当前处理 job、当前页版本、完整 active 题目序列、一题一锚点、顺序一致、置信度至少 0.95 且页面无质量告警的集合，缺失或漂移继续交给教师。处理任务完成时会直接确认满足上述条件的确定性区域并写 `processing.segmentation.auto_confirm` 审计，不再要求教师重画或点击批量确认；这只确认答题区域，不确认题目/答案/评分标准、最终成绩或发布状态。两个相关完整测试文件最终 `24 passed`；0035 SQLite 升降级与 Alembic 单 head 契约 `3 passed`，PostgreSQL `0034→0035→0034→0035` 通过，最终 single head 为 0035；Ruff 与改动运行时代码 strict mypy 通过。隔离预览对当前 PDF 的新 job `21d37e70-d31b-40ce-a386-944267ab0fc1` 已 completed：正文五个题号均为 `pdf_text`、置信度 0.99、page_version 9，并生成五个 `confirmed/system_auto/QUESTION_ANCHOR` 区域；页脚“第 1 页”以 0.54450 和 `LOW_ANCHOR_CONFIDENCE` 留作拒绝证据，未污染题号 1；审计 resource 为该 processing job、policy `strict-auto-confirm-v2`、region_count 5。API 与批改页 HTTP 200，六服务 healthy。准确边界：当前实跑只证明带文本层 PDF；纯扫描件需要可用的真实 OCR，低置信时仍需教师处理。未触碰 `ahamark-user-test-ac7ceb6` 或真实学生数据，未确认正式内容、发布作业/成绩或释放成绩。

2026-08-06 批改页面“过亮”误报与自动校正修复（完成、未暂存/提交/推送）：独立合成试用栈中的 `03_学生答卷_高质量示例.pdf` 原由旧 `submission-processing-v1` 仅按整页平均亮度 `252.04839` 判定 `TOO_BRIGHT`；该页对比度 `22.58148`、清晰度 `31.40999`，实为白底稀疏内容造成的误报，且任意 quality warning 会继续阻止高置信区域自动确认。现将预处理升级为 `submission-processing-v2`：用整页灰度直方图区分白色背景与有效内容亮度，真实浅灰过曝内容先执行自适应动态范围拉伸，再做既有温和对比度与中值滤波，并以处理后指标重新判定 `TOO_BRIGHT/LOW_CONTRAST`；原始 rendered artifact 仍永久保留，只有处理后仍异常才继续 fail closed，不会自动确认区域、答案或成绩。重试/旋转入口会同步升级已有 job 的 config version，避免页证据为 v2 而 job 仍声称 v1。新增白底黑字不误报、浅灰过曝自动拉回且复测清除告警、旧 job 重试升级回归；`test_submission_processing.py` 为 `11 passed`，automatic-confirmation 回归为 `9 passed`，改动 Python 文件 Ruff check/format 与 strict mypy、E2E 脚本 Node syntax、相关 JS/TS Prettier、`git diff --check` 均通过。聚焦 Vitest 未进入用例执行，因为临时 Alpine 依赖环境缺少 npm optional Rollup musl 包；前端运行时代码未修改，不能将该次记为测试通过或产品失败。实际合成 PDF 探针为原始整页亮度 `252.11166`、有效内容亮度 `117.33985`、处理后内容亮度 `125.36553`、对比度 `24.70854`、warnings `[]`，证明无需破坏性曝光修正即可消除误报。隔离栈重建后已通过正式 retry 接口重新处理当前页：job `attempt=6`、`config_version=submission-processing-v2`、状态 completed；page `page_version=5`、`preprocessing_version=submission-processing-v2`、状态 completed、`quality_warnings=[]`，API `/ready` 与批改页均 HTTP 200，六服务 healthy。未触碰 `ahamark-user-test-ac7ceb6`、真实学生数据或正式成绩，未自动确认、发布或释放成绩。

2026-08-06 synthetic guard strict JSON-like 边界 P2 复审修复（完成、未暂存/提交/推送）：第三次复审确认上一版仅用普通键枚举与直接字段读取，尚未完整拒绝继承字段、Symbol/非枚举键、访问器、非 plain prototype、异常 Proxy、稀疏/扩展数组和重复 target name。现统一新增 descriptor-only 输入解析：顶层 options 与 target 仅接受 `Object.prototype` 或 null prototype 的非 Proxy plain record；使用 `Reflect.ownKeys` 审计全部自有键，必需字段必须 own，允许字段必须是 enumerable data property，读取只使用 descriptor.value，因此不会触发 getter。targets 仅接受 `Array.prototype` 的 1–8 项标准稠密数组，除 `length` 和规范索引外不允许任何键，元素必须是 enumerable data property；冻结对象/数组仍允许，因此不要求 writable/configurable。target name 使用安全环境键格式并在构造 null-prototype origins 前全局去重，拒绝 last-write-wins。Proxy 在任何反射 trap 前以 `*_PROXY_UNSUPPORTED` 稳定拒绝，其他反射异常统一为 `*_REFLECTION_FAILED`；record、property、array shape/count/element、重复名称分别使用稳定 reason code，不回显对象或 URL。直接 JavaScript 契约覆盖继承必需字段、object-literal `__proto__`、null-prototype/frozen 合法记录、own `__proto__`、顶层/target Symbol、非枚举未知键、必需/未知 getter、副作用探针、跨 policy 重名、空/超限/稀疏/附加键/Symbol/访问器/自定义 prototype 数组、Date/Map/class 与 ownKeys/descriptor Proxy，最终相关契约 `59 passed`。Node 三文件 `--check`、三文件 Prettier、Python 契约 Ruff lint/format 与 `git diff --check` 全部通过；DB/lockfile/staging 无变化。本轮未启动 Docker、浏览器或网络，未记录密钥或真实数据，未实际发布。

2026-08-06 synthetic guard 权威命名策略 P2 复审修复（完成、未暂存/提交/推送）：第二次复审确认“调用方传完整 allowlist”仍把最终授权集合留在入口，未来可通过增加 loopback 端口扩大权限。现将 `assignment_preprod`、`business_web`、`business_api` 三个策略及其协议/端口放入 `scripts/synthetic_browser_guard.mjs` 模块私有冻结数据；调用方只传 policy 名、变量名与原始 URL，不再接收或复制 origin/port/protocol 集合。guard 会拒绝未知 policy，以及顶层或 target 的任何未知键，旧 `allowedOrigins`/`allowedPorts`/`protocols` 均以稳定 reason code fail closed 且不回显 URL；原始 ASCII/canonical 与解析后二次 invariant 保持。契约覆盖三策略全部 21 个合法 host×port 组合、交叉协议/端口、未知 policy、带旧扩权参数的端口 22，以及三个入口的精确 policy 选择，最终 `53 passed`。Node 三文件 `--check`、三文件 Prettier、Python 契约 Ruff lint/format 与 `git diff --check` 全部通过；`ahamark.db`、根 lockfile hash 不变，Web lockfile 不存在且三者无 diff，暂存区为空。本轮未启动 Docker、浏览器或网络，未记录密钥或真实数据，未实际发布。

2026-08-06 synthetic URL P2 / sink-order P3 复审修复（完成、未暂存/提交/推送）：聚焦复审确认上一版 guard 在 `new URL(raw)` 后才按规范化 hostname 判断，非标准 IPv4、Unicode/百分号主机等原始表示可能被 WHATWG 归一化为 loopback；同时入口契约只覆盖目录创建与 Chromium，不能把“无 artifact”夸大为直接观测 Docker/network 未调用。该轮先改为解析前严格可打印 ASCII、无尾斜杠的 canonical origin，原始检查通过后才调用 `new URL`，并要求 origin/protocol/hostname/host/port 全部与原文一致；拒绝 reason 使用稳定 code，不回显原始 URL。协议与端口授权随后已由更新的内部权威命名策略取代调用方 allowlist，最终状态以本条上方最新账本为准。契约补齐非标准 IPv4、制表符、Unicode/圈字、百分号、尾点、空 query/fragment、大小写、前后空白、反斜杠、userinfo 与 IPv4-mapped IPv6；源序契约分别覆盖 `execFileSync`、Compose/Docker helper、`page.goto`、`fetch`、`apiJson` 等实际存在的首个 sink，并明确区分 assignment 不存在的 sink。真实入口无 ALLOW 子进程只实证退出码/reason code 与无 artifact；未直接 spy Docker/network，但源序契约证明 guard 调用早于这些 sink。本轮没有启动 Docker、浏览器、网络或实际发布。该轮验证为相关契约 `50 passed`，Node 三文件 `--check`、三文件 Prettier、Python 契约 Ruff lint/format、`git diff --check` 全部通过；`ahamark.db`、根 lockfile hash 不变，Web lockfile 不存在且三者均无 diff；未记录密钥或真实数据。

2026-08-06 P3 门禁首个副作用前实证（完成、未暂存/提交/推送）：除纯 guard 子进程矩阵外，契约测试会直接启动两个真实脚本入口但故意不提供 `ALLOW_SYNTHETIC_MUTATIONS`；为它们指定临时 evidence/artifact 路径，并已实证进程以明确 ALLOW 错误退出且目录/文件均不存在。另由精确源序契约证明 guard 调用早于 `fs.mkdirSync`、Docker helper、Chromium 和服务器 mutation/network sink；本轮没有通过 spy 直接观测 Docker/network helper。测试使用的占位密码仅存在于子进程环境，不写日志/evidence、不连接服务器；未以 `ALLOW_SYNTHETIC_MUTATIONS=1` 运行真实浏览器流程，也未创建、确认或发布作业。聚焦契约当时为 `49 passed`；第一次使用 pytest 默认 Windows 临时目录时因既有 ACL 在 fixture setup 前失败，第二次工具总时限中断，改用唯一外部系统 Temp/basetemp 后完整通过，入口实证单测另为 `1 passed`。

2026-08-06 浏览器 E2E synthetic mutation P3 补强（完成、未暂存/提交/推送）：最终只读审查发现两个会确认/发布/写状态的浏览器脚本仅“记录 synthetic”，未在 mutation 前 fail closed。现新增共享无副作用 `scripts/synthetic_browser_guard.mjs`，两个入口必须满足 `ALLOW_SYNTHETIC_MUTATIONS === "1"`；教师邮箱必须匹配 reserved `*.synthetic.invalid` 规则；URL 仅允许明确协议、loopback host（localhost/127.0.0.1/[::1]）与脚本列出的测试端口，禁止 userinfo/path/query/fragment；business 还校验 synthetic Compose project、run prefix 和 marker suffix，显式拒绝 `user-test`，并把通过后的非敏感 guard evidence 绑定到输出。密码和内部 token 不进入 evidence。正/负向契约覆盖缺失、空值及非 `1` ALLOW、非 synthetic 邮箱、远程/异常/带 userinfo URL、非法 project/run/marker 和合法本地 IPv4/IPv6 配置；源序契约要求 guard 调用早于目录创建、Docker helper 与 Chromium，无 ALLOW 入口子进程仅实证明确退出和无 artifact，并未直接 spy Docker/network。最终验证当时为：契约 `49 passed`，Node 三文件 `--check`、三文件 Prettier、Python 契约 Ruff lint/format、`git diff --check` 全部通过；`ahamark.db` 与根 lockfile SHA-256 仍分别为 `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`、`8FAC7F389094E4978DDA7C04200D325C031565B3FFDC581ADA3295808C18EDFF`，Web lockfile 不存在且三者均无 diff。未记录密钥、未使用真实数据、未运行真实 Provider 或实际发布流程。

2026-08-06 Structured-only 最终收口（完成、未暂存/提交/推送/部署）：依赖边界已修复为 0687 独立普通目录 `node_modules`，旧 16d7 Junction target 删除前后 22,554 files 与三项 hash 不变；异常 `apps/web/16d7` 仅含 ignored standalone 依赖并已精确删除，最终 Next build 未重建且 standalone 旧 worktree 路径 0 命中。隔离真实 Chromium E2E run `structured-only-0687-final9-20260806034501339` 从 fresh PostgreSQL 完整通过 A–F，Alembic `0034_structured_rubric_authority`，证明 Structured Set/唯一发布/active Set 固定批改链；使用 Fake/Codex local synthetic suggestion-only，真实 Provider 未调用，`确认结果 clicked=false`、`grade_release_write_attempted=false`。最终验证：后端 `613 passed, 18 skipped, 345 warnings`；前端 `27 files / 168 tests passed`；browser 静态契约 `42 passed`；Next build 19 页；Prettier、ESLint、TypeScript、全仓 Ruff lint、strict mypy（109 files）、Alembic single head、changed-Python Ruff format、`git diff --check` 全部通过；产品 runtime 259 files 的 Legacy 精确扫描 0 命中，脚本 10 命中均为禁止 Legacy UI/异常码的负向断言。全仓 `ruff format --check` 另报告 36 个既有/历史 CRLF 文件会被机械重排（含受保护旧迁移），未为格式门禁越权改写；本轮实际修改的 3 个 Python 文件 formatter 全部通过。`ahamark.db` SHA-256 `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`、根 lockfile `8FAC7F389094E4978DDA7C04200D325C031565B3FFDC581ADA3295808C18EDFF`，Web lockfile 不存在且三者无 diff。临时 Compose project 容器/网络/volume 均 0、删除持久卷数 0；既有用户测试栈未变化。最终产品 diff 风险审计没有新增自动确认、自动发布、定稿或成绩释放路径；暂存区为空。远端分支仍为 `04708deecb197a37425721f25842255735b9dbb8`，PR #1 仍 OPEN/Draft/CLEAN；按授权边界未 add/commit/push/merge/deploy 或改变 Draft 状态。

2026-08-06 最终后端全量验证（未暂存/提交/推送）：在系统 Temp 下唯一稳定根 `ahamark-final-root-20260806-0352` 运行仓库标准单进程全量 pytest，数据库所有权守卫全程有效；最终 `613 passed, 18 skipped, 345 warnings`，耗时 `39:16`，退出码 0，且守卫明确报告 `affected ahamark.db unchanged`。首次尝试把 TEMP 指向 worktree 内被安全守卫在 collection 前拒绝（0 tests），未放宽守卫；最终合规运行未复现先前 marker 丢失。下一步完成 Ruff、strict mypy、Alembic、Legacy/hash/path 和 diff 最终门禁。

2026-08-06 最终前端全量验证（进行中、未暂存/提交/推送）：前端全量 `27 files / 168 tests passed`，ESLint、TypeScript、Next 15.5.9 production build（19 个静态页面）通过；首次 Prettier check 仅报告本轮 Review Bundle v2 改动的 4 个文件格式不一致，已用仓库 Prettier 机械格式化并复查通过。Next 仍输出既有 SWC lockfile patch warning，但 build 退出码为 0；待最终 hash 守卫确认 lockfile、`ahamark.db` 和异常 standalone 路径均未变化/重生。

2026-08-06 Structured-only 隔离浏览器 E2E 完成（未暂存/提交/推送）：唯一 project `ahamark-structured-only-0687-20260806-01` 在显式 `desktop-linux` context、独立端口、PostgreSQL/Redis/MinIO tmpfs 且持久卷 0 的环境中从空库完整通过 A–F 六阶段（run `structured-only-0687-final9-20260806034501339`，证据位于 Git 忽略的 `test-results/structured-only-e2e-0687/evidence-final9.json`）。覆盖登录、创建班级/作业、基础信息后进入第 2 步上传、文件生成、两题题号/分值/知识点/答案/Structured Rubric、Bundle v2、当前 Structured Set（2 items/10 分/0 blocker/0 warning）、教师唯一一次“确认并发布”、创建批改批次、两份合成提交、4 个 Codex local suggestion-only 结果全部固定同一 active Set、四项教师复核及 complete snapshot；“确认结果”唯一且可用但 `clicked=false`，`grade_release_write_attempted=false`，真实 Provider 未调用。fresh PostgreSQL Alembic 为 `0034_structured_rubric_authority`。临时容器/网络已精确删除，project 容器/网络/volume 均 0，删除持久卷数 0；既有 `ahamark-user-test-ac7ceb6` 六个容器 ID/Exited 状态和三个 volume 名称与基线一致，未启动、停止、复用或删除。该验证只证明 Fake/Codex 合成工作流与真实 Chromium 编排，不证明真实 Provider 质量，也未使用真实学生数据或释放成绩。下一步完成最终前后端全量与静态门禁。

2026-08-06 隔离 E2E 每项复核独立起点（进行中、未暂存/提交/推送）：第八次空 tmpfs 重跑仍稳定完成三项 PUT，第四项在自动前进后的复用组件中未触发请求。为彻底隔离 UI 自动前进、筛选和异步 `load()` 状态，现将四项合成教师复核改为每项先 reload，再显式选择“全部”、精确学生、精确题号并等待预期 answer ID，之后才点击评分动作；仍由真实浏览器按钮发起写入，不使用 API 旁路。静态契约新增每题 reload 与精确学生选择约束。

2026-08-06 隔离 E2E 复核总计文案对齐（进行中、未暂存/提交/推送）：第七次空 tmpfs 重跑已由隔离 API 日志确认四个不同 answer 的教师复核 PUT 全部 `200`，脚本也完成对应 workspace 回读；最终仅因当前页面顶部源码为“已检查 4/4”，旧驱动等待“已复核 4/4”而超时。现把动态总计断言精确对齐“已检查 4/4”，仍要求四项全部完成、唯一“确认结果”按钮可用且保持 `clicked=false`。

2026-08-06 隔离 E2E 复核网络证据稳定化（进行中、未暂存/提交/推送）：第六次空 tmpfs 重跑已在“全部”筛选下保持稳定列表，但 Playwright `waitForResponse` 仍随机错过已经发生的复核响应。现把四种教师复核动作统一到同一 helper：点击前捕获精确 answer ID 的 PUT request，再等待该 request 自身的 response，硬断言 HTTP 200；主观题继续解析响应 body，随后仍按 answer/decision/score/criterion/Structured Set/Rubric version 完整回读。新增静态契约要求 request→response 关联，不以日志或 UI toast 代替运行时验证。

2026-08-06 隔离 E2E 复核列表稳定化（进行中、未暂存/提交/推送）：第五次空 tmpfs 重跑中三项教师复核 PUT 均为 `200`，页面显示 `已复核 3/4`，最后一项未发请求；截图确认驱动仍处在默认“需检查”筛选，已复核答案会在每次保存后从导航动态消失，使按原 workspace 序号遍历的学生/题目 locator 重排。现仅在合成 E2E 逐项复核前显式选择“全部”，等待稳定显示两名学生，再继续用 workspace answer ID 精确同步和逐项持久化断言；产品默认筛选与教师行为不变。

2026-08-06 隔离 E2E 复核面板身份同步（进行中、未暂存/提交/推送）：第四次空 tmpfs 重跑的 API 日志证明第 2 名学生第 1 题复核 PUT 实际 `200`，但 Playwright 仍等待另一 answer ID 的响应；根因是自动前进后，点击学生/题目与 React 面板重绘之间存在竞态，旧驱动只等可能与上一面板同名的“第 N 题”标题便读取 `data-answer-id`。现从已验证的 workspace 按 submission/question 取得预期 answer，并在任何评分动作前强制等待面板 `data-answer-id` 精确匹配；新增静态契约，防止未来只凭重复标题继续。此修复不会接受错误 answer 的成功响应，反而确保教师操作落在预期学生与题目。

2026-08-06 隔离 E2E 短暂提示时序修正（进行中、未暂存/提交/推送）：第三次空 tmpfs 重跑再次在保存后提示等待超时；代码顺序确认驱动是在成功 PUT、响应 body、完整 workspace 回读、最终分数、decision、criterion、Structured Set ID 和 Structured Rubric version 全部核验完成后，才寻找短暂 toast，因此 toast 已可能消失。删除该非持久 UI 提示等待，保留每项成功响应与持久化强回读，并继续要求页面最终显示 `已复核 4/4` 和唯一、可用但不点击的“确认结果”；不会把失败响应或未保存状态视为通过。

2026-08-06 隔离 E2E 自动前进提示对齐（进行中、未暂存/提交/推送）：第二次空 tmpfs 重跑已完成 active Set 固定版本断言、两名学生/两题导航以及第 1/4 项教师复核写入；写接口与回读断言均成功，截图显示当前默认自动前进提示为“已保存，已进入下一题”，旧驱动只等待非自动前进分支的“复核结果已保存”。现让驱动精确接受组件源码定义的两种成功提示，不接受错误或无提示；最终仍需达到 `已复核 4/4`，且不会点击“确认结果”或释放成绩。

2026-08-06 隔离 E2E 复核导航文案对齐（进行中、未暂存/提交/推送）：从空 tmpfs 重跑已越过发布、打开批次、上传/处理两份合成提交并生成 Codex suggestion-only 结果，失败只因驱动按旧文案寻找“提交…”导航；失败截图与当前 JSX 一致显示提交维度按钮为“学生 1/学生 2”。现将选择器收紧为精确 `学生 + 序号`，题目按钮仍按“第 N 题”定位，选中态和结果 Set/版本断言保持不变；本轮 `grade_release_write_attempted=false`，未确认或释放成绩。

2026-08-06 Bundle v2 静态契约收口（进行中、未暂存/提交/推送）：批次入口选择器修正后，浏览器静态契约为 `40 passed, 2 failed`；两项失败均仍硬编码旧 8 类逐项确认和旧写入循环。现把契约对齐现行 5 类自动常规确认，并明确禁止 `file_roles/answer_sources/paper_version` 被误算为确认项；它们继续由 Bundle blocker、Structured Set current 指纹以及 `blocking=0/warning=0` 的发布硬门禁覆盖。测试同时要求 E2E 只读映射 Bundle confirmation、不调用逐项确认写入，不削弱发布条件。

2026-08-06 隔离 E2E 批次入口文案对齐（进行中、未暂存/提交/推送）：空 tmpfs 全流程已通过登录、创建作业、第 2 步上传、Structured 草稿与 Set、唯一一次教师确认并发布以及批改批次创建；失败截图显示当前产品入口按钮为“打开批次”，旧浏览器驱动仍寻找“进入批次工作台”。现只将驱动选择器对齐当前可见文案，不改变产品运行时、active Set 固定规则或成绩状态；将从空 tmpfs PostgreSQL 重跑完整流程。

2026-08-06 v2 常规确认集合对齐（进行中、未暂存/提交/推送）：Bundle v2 浏览器运行中自动确认已稳定出现 `classes/due_at/total_score/reference_answers/structured_rubrics`；旧驱动额外等待 `file_roles/answer_sources/paper_version`，但这些已不在运行时组件的确认集合中，其安全性由 Bundle blocker 与指纹直接覆盖。现把 E2E required confirmations 对齐为前端权威的 5 类；仍逐项验证 current confirmation hash/origin，文件用途、答案来源和试卷版本仍必须没有 blocker，不能因移除旧显式确认而绕过。

2026-08-06 E2E 常规核对切换为自动投影（进行中、未暂存/提交/推送）：一步到位前端在 Bundle v2 下会调用 `auto-confirm` 并隐藏逐类确认控件，旧驱动仍等待/点击这些控件，与产品决策相冲突。现改为浏览器保持页面运行时只读轮询 review session 与 Bundle，要求运行时权威的五类常规确认全部由服务端出现，并逐项校验 Bundle 当前 fingerprint/origin；E2E 不再写任何逐类确认，随后仍等待自动 Structured Set 和唯一“确认并发布”。

2026-08-06 Review Bundle v2 聚焦验证：中央核查与答案/Rubric generation review 前端联合 `2 files / 56 tests passed`，TypeScript 通过；`ahamark.db` 未变化。下一步重建隔离 Web 镜像后重新跑真实浏览器流程。

2026-08-06 Review Bundle v2 前端契约修复（进行中、未暂存/提交/推送）：fresh PostgreSQL 浏览器 E2E 精确观察到后端 `/review-bundle` 返回 `schema_version=assignment-review-bundle-v2`，前端组件和 TypeScript 类型仍只接受 v1，导致当前 Bundle 被误判为版本不一致、自动常规核对不启动并显示“重新加载当前作业”。现将运行时组件、API 类型、两组前端 fixture、浏览器驱动和静态契约统一到后端现行 v2；历史 `docs/business-e2e-verification.json` 作为旧运行证据不改写。该修复不会接受未知 schema，不放宽 Bundle 指纹或发布门禁。

2026-08-06 Fake 分值更新契约修正（进行中、未暂存/提交/推送）：题目“PATCH”路由实际使用完整 `QuestionInput`，只发送 `max_score` 被 Pydantic 422 拒绝；现用刚刚只读回取的题号、题型、正文/LaTeX、难度、父题和知识点原值组成完整 payload，只将空分值补为 5，并继续核验响应。该驱动修正不会把缺失字段静默清空，也不改变产品接口。

2026-08-06 Fake generation 分值边界（进行中、未暂存/提交/推送）：第 1 题 Structured Rubric 页面创建失败的精确 API 原因是 `422 RUBRIC_POINTS_MISMATCH`；只读作业详情确认 Fake generation 题目的 `max_score=null`，页面默认 1 分，而第 2 题为 5 分。合成 E2E 现仅在生成题分值为空时 PATCH 为 5 分，并要求 HTTP 200 及响应数值精确为 5，使两题合计与作业 10 分一致；不会接受非空冲突值。该补值明确属于 Fake 测试夹具，不是实际 Provider 分值质量证明，后端 Rubric/总分门禁保持 fail-closed。

2026-08-06 Fake generation 区域边界（进行中、未暂存/提交/推送）：纠正后的唯一 generation 第 1 题没有携带页面区域，驱动在进入发布/批改前按 fail-closed 断言停止。为验证后续 Structured-only 编排，合成 E2E 现仅在该题 region 为空时，用上传试卷的真实测试页 ID 写入固定 0–1 坐标范围的测试区域并要求 API 201；第 2 题仍由浏览器区域编辑器写入。该步骤明确是 Fake/Codex 合成夹具，不宣称真实 Provider 的题目分区质量，也不移除发布或批改对 region 的要求。

2026-08-06 一步到位 E2E 空题目表单适配（进行中、未暂存/提交/推送）：纠正输入链后，generation 前的第 4 步尚无题目，页面已直接处于创建态且不会显示用于从“编辑已有题目”切换出来的“新增题目”按钮；驱动因强制寻找该按钮而超时。现仅在按钮可见（已有题目被选中）时点击，否则直接填写空创建表单，仍由“添加题目”接口创建第 2 题。

2026-08-06 一步到位 E2E 输入链纠正（进行中、未暂存/提交/推送）：失败后的 API 证据显示旧驱动先由 OCR 确认第 1 题，再让 generation 将同内容候选物化为另一道第 1 题；Bundle 因真实的 `TOTAL_SCORE_MISMATCH=15/10` 及新题缺答案/Rubric 而正确阻断，不能把它当作 clean 409 自动恢复。现将恒定启用的一步到位分支改为只由“上传文件 → generation”生成第 1 题，随后回读该题与教师新增的第 2 题、确认每题 region，再通过 Structured 编辑器形成答案/Rubric；非一步到位诊断模式仍保留 OCR 分支。这样消除的是测试自己制造的重复输入，不拒绝候选、不隐藏 blocker、不放宽发布门禁。

2026-08-06 隔离浏览器 E2E 第五轮诊断（进行中、未暂存/提交/推送）：Structured generation 已完成并回到第 5 步；失败截图明确显示两道正式题均为“参考答案：已确认 / 评分标准：已确认”，同时页面顶部还有一张待处理的生成建议卡。驱动此前把所有 `question-review-card-*`（包括 suggestion）都要求为已确认，因而在建议卡失败。现将核查精确限定为带“题目、答案和评分标准已确认”的正式题卡，并继续要求恰有两张且逐张包含题号、已确认答案和已确认评分标准；生成建议仍按既有 suggestion-only 处置，不隐藏或自动确认。

2026-08-06 browser E2E 静态契约稳健性（进行中、未暂存/提交/推送）：移除旧第 5 步分支后，三项静态测试因按全脚本中 `if (singleContinueProof)` 的固定“第 3 次出现”切片而失败，未发现运行逻辑断言失败。现改为分别在已隔离的 E/F stage 文本内定位该分支，使测试约束目标不受前面无关分支数量影响；待 Ruff 与 42 项契约复验。

2026-08-06 隔离浏览器 E2E 第四轮诊断（进行中、未暂存/提交/推送）：第 2 题与区域均已真实写入并由作业详情回读，但旧驱动在任何 generation job 存在前进入第 5 步；服务器正确返回 `409 GENERATION_REQUIRED`，页面显示“无法取得当前审查内容”，因此题目选择器为空。现按 Structured-only 流程修正驱动顺序：从作业详情精确取得两题 ID 并确认每题已有 region，直接进入各题 Structured Rubric 编辑器确认答案/Rubric；第 6 步生成完整草稿并处理合成建议后，再回到第 5 步核验题号、分值、知识点、答案和评分标准，最后进入集中发布检查。删除的只是已不存在的旧逐题评分标准步骤依赖及重复 region 写入；没有绕过 generation/readiness 门禁。

2026-08-06 隔离浏览器 E2E 第三轮诊断（进行中、未暂存/提交/推送）：驱动已成功创建并重新选中第 2 题，题号、知识点和题目内容断言均通过；页面按数据库精度回填分值 `5.00`，旧驱动用字符串 `5` 严格比较而失败。现改为数值等价且仍精确要求 5 分，不接受分值缺失或偏差；待空 tmpfs 重跑。

2026-08-06 隔离浏览器 E2E 第二轮诊断（进行中、未暂存/提交/推送）：驱动已通过第 2 步跳转、文件上传和 OCR 确认，进入第 4 步后仍按旧表单语义直接填充并寻找“添加题目”；当前产品为保护已选题目，默认主按钮是“保存题目”，只有教师先点“新增题目”才清空并进入创建态。现仅修正合成驱动先显式点击“新增题目”，再填写第 2 题并使用创建按钮；不更改产品保存/脏编辑保护。仍将重建空 tmpfs 后从头验证。

2026-08-06 隔离浏览器 E2E 首轮诊断（进行中、未暂存/提交/推送）：真实浏览器已观察到新建作业保存后实际导航到 `/assignments/{id}/edit?step=2`，产品行为正确；驱动原 `**/assignments/*/edit` glob 不接受查询参数，因此在进入上传前仅因 URL 等待超时。现将该测试等待收紧为只接受同一路径及可选 query 的正则，并继续要求页面出现“上传试卷”标题；待精确销毁本轮容器/网络（不带 `-v`）、以空 tmpfs 重建后重跑，避免复用半程合成数据。

2026-08-06 隔离浏览器 E2E 证据补强（进行中、未暂存/提交/推送）：主 business browser 驱动现明确等待新建作业保存后的第 2 步“上传试卷”，并在批改建议形成后逐项断言结果的 `structured_rubric_set_id` 等于本轮发布时的 active Structured Set；这些断言只增强合成 E2E 证明，不改变产品运行时、教师确认权或成绩状态。唯一 tmpfs Compose project 已在显式 `desktop-linux` context 启动，六服务 healthy；PostgreSQL/Redis/MinIO 均为 `Mounts=[]` 且仅使用 tmpfs，项目持久卷数为 0。待完成正确测试角色的迁移查询、浏览器实跑及本轮容器/网络精确清理。

2026-08-06 依赖路径与最终全量补验（进行中、未暂存/提交/推送）：开始前未发现 node/npm/Next/Vitest 进程。0687 根 `node_modules` 已精确确认为 Junction/ReparsePoint，target 为 `D:\OpenAIData\.codex\worktrees\16d7\AhaMark\node_modules`；target 存在且删除前文件数为 22,554，16d7 根 `package.json` / `package-lock.json` 与 target `.package-lock.json` SHA-256 分别为 `ced11592d69dd97aca3c9af53a6121abfdbedffd4a6255f5b4292aa322538551`、`8fac7f389094e4978dda7c04200d325c031565b3ffdc581ada3295808c18edff`、`d66bea619841506719e05e8fe60661bffa1c5e7322513b24ddb9f2f48178a735`。`Remove-Item -LiteralPath` 因 PowerShell Junction NullReference 未产生删除，随后从同一 `Get-Item -LiteralPath` 精确取得的 `DirectoryInfo` 执行非递归 `Delete()`，只移除 0687 Junction；删除后 target 仍为 22,554 个文件且三项 hash 逐项不变。0687 根 `npm ci` 已独立安装 468 packages，`node_modules` 为普通非 ReparsePoint 目录；Next 15.5.9、React 19.1.1、Vitest 3.2.7、ESLint 9.39.5、TypeScript 5.9.3，根 lockfile、Web lockfile不存在状态与 `ahamark.db` hash 均 unchanged；npm 报告既有 4 个 high advisories，未运行会改依赖/lockfile 的 fix。`apps\web\16d7` 删除前精确位于当前 `apps\web` 内，为普通目录；31 files / 17 dirs / 170,561 bytes 全部位于 `16d7\AhaMark\node_modules`，仅含被 Git 忽略的 Next/styled-jsx standalone 依赖残留，tracked/unignored/进程引用均为 0；已仅删除该工作区异常目录。独立 Next production build 已成功生成 19 个静态页面，未重建 `apps\web\16d7`；`.next\standalone` 顶层仅为当前 `apps/node_modules/package.json` 结构，内容中的 worktree ID 仅有 `0687`，`D:\OpenAIData` 与非 0687 worktree 路径均为 0。依赖包内一次文本 `16d7` 命中是 crypto-browserify 测试向量哈希片段，不是路径；构建仍有仓库既有 SWC lockfile patch warning，但退出码为 0 且全部守卫 unchanged。下一步执行前后端全量、静态门禁、Legacy runtime 扫描与隔离 E2E。

2026-08-06 Legacy browser runtime 扫描修复（进行中、未暂存/提交/推送）：首次扫描把 Structured Rubric 内部正常使用的 `rubric_version_id` 误算为 Legacy，收紧到 Legacy 类、表、binding 路由/按钮和异常码后，唯一真实运行残留是 `scripts/assignment_generation_browser_e2e.mjs` 仍操作已删除的 publication binding 两按钮并记录 `legacy_binding`。现已将该脚本改为等待 `structured-rubric-set-summary`、断言页面无 Legacy binding/兼容版本/旧异常码、只点击一次“确认并发布”，并验证发布后 `active_structured_rubric_set_id`；新增静态契约防止旧 binding 选择器与旧双按钮发布流程回归。该修改只修测试 E2E 驱动，不改变产品 API、确认/发布门禁或成绩写入；待聚焦测试、Ruff/Prettier 和收紧后的 runtime 零命中复验。

2026-08-06 隔离 business browser E2E 驱动切换（进行中、未暂存/提交/推送）：进一步审计发现主 `business_browser_e2e.mjs` 虽已使用 Structured Set API，但发布 UI 仍按旧“准备发布 → dialog → 教师确认并发布”双按钮流程执行，且脚本内部 Docker provenance 命令没有显式 context、固定使用原 Compose 文件。现增加 `BUSINESS_E2E_COMPOSE_FILE` 与 `BUSINESS_E2E_DOCKER_CONTEXT`，所有脚本内 Docker/Compose/inspect/psql 调用统一带 `--context`；发布步骤改为等待唯一“确认并发布”按钮、核验 Structured Set/current/0 blocker、断言页面无 Legacy binding/兼容按钮/旧异常码并只发起一次 publish POST。脚本还在创建后回读第 2 题题号、分值、知识点和内容，并在第 5 步逐卡核验参考答案与评分标准已确认；新增静态契约禁止旧双按钮/dialog 回归。格式化后的 browser E2E 静态契约 `42 passed`，Ruff/Prettier 通过，`ahamark.db` unchanged。仅修改合成 E2E 驱动和测试，不自动确认真实内容、不发布成绩；待实际唯一 tmpfs Compose 运行验证。

2026-08-06 隔离 Compose 启动边界（进行中）：唯一 project `ahamark-structured-only-0687-20260806-01` 的镜像构建成功，但首次启动在创建默认 network 前被 Docker `all predefined address pools have been fully subnetted` 阻断；确认该 project 容器/network/volume 仍全为 0，未触碰现有对象。只读列出全部现存子网并检查 Windows 路由后，选择未占用且无路由冲突的 `10.251.68.0/24` 写入 Git 忽略的临时 Compose；现有 `ahamark-user-test-ac7ceb6` 继续保持六容器 Exited，三 volume metadata hash 基线为 `b9262663ae95ea06a9fc7e492f63e11f97faacef55d39a905789a83fac5c9755`。待以显式 `desktop-linux` 重试并核验实际 tmpfs/零 volume。

2026-08-06 Structured-only fresh PostgreSQL 补验（完成、未暂存/提交/推送）：Docker Desktop 4.83.0 / Engine 29.6.2 通过显式 `desktop-linux` context 健康；Codex sandbox 的 default context 与 `~/.docker` ACL 才是此前误报来源。唯一临时容器 `ahamark-structured-fresh-0687-20260806-01` 使用 `/var/lib/postgresql/data` tmpfs、独立端口 `55473`，检查为 `Mounts=[]`，未挂载、创建或删除任何持久卷；空库完整 `0001 → 0034_structured_rubric_authority`、受保护历史全路径（`1 passed`）以及真实 PostgreSQL `0034 → 0033 → 0034` 均通过，最终仅存在 Structured Set 表且四个 Legacy/binding 表不存在。双向补验最初暴露 readiness Legacy 外键显式名超过 PostgreSQL 63 字符上限；0033 探针确认原始 PostgreSQL 名为 `assignment_publish_readiness_snap_legacy_rubric_version_id_fkey`，0034 downgrade 已改用该精确等价名称并增加防回归测试。0034/0006 迁移、中央发布和 active Set 联合契约 `23 passed`，另一次修复前迁移聚焦 `10 passed`；Ruff check/format-check、Alembic 单 head `0034_structured_rubric_authority` 和 `git diff --check` 均通过，`ahamark.db` 哈希 unchanged。临时容器已按精确名称核对后删除，持久卷删除数为 0；现有 AhaMark 容器和卷未启动、复用、删除或修改。根 `node_modules` Junction 指向 `D:\OpenAIData\.codex\worktrees\16d7\AhaMark\node_modules`，本轮只记录路径边界，未删除或替换；为避免触碰已退出的用户测试栈及放大 Junction 的 Next standalone 路径问题，本轮不运行真实浏览器 E2E。GitHub 远端 `codex/grading-confirm-results` 仍为 `04708deecb197a37425721f25842255735b9dbb8`，PR #1 仍 OPEN/Draft/MERGEABLE；本轮没有自动确认、发布、写成绩、放宽门禁、合并、部署或改变 PR 状态。

2026-08-05 Structured-only 最终交付审计收口：后端 `610 passed, 18 skipped, 345 warnings`；前端 `27 files / 168 tests passed`；Next build 19 个静态页面；Prettier、ESLint、TypeScript、Ruff、strict mypy（109 files）、Alembic 单 head `0034_structured_rubric_authority`、git diff --check 全部通过。非历史迁移的 API/worker/前端/脚本 Legacy 精确扫描为 0；0006 固定 DDL hash 回归与 0034 迁移边界通过；`ahamark.db`、根/Web lockfile 无 diff。当时未执行 fresh PostgreSQL 容器 upgrade 或真实浏览器 E2E；离线 PostgreSQL migration SQL 与 SQLite upgrade/downgrade 边界已通过。Structured-only 主体已提交并推送为 `04708deecb197a37425721f25842255735b9dbb8`；未合并、部署或改变 Draft PR 状态。

2026-08-05 最终后端全量验证（未提交）：Structured-only 切换及上述夹具/迁移修复后，后端全量 `610 passed, 18 skipped, 345 warnings`，耗时 `44:53`，无失败或 setup error，`ahamark.db` unchanged。此前 27 个失败已全部在 53 项联合簇中复验通过。Ruff import 排序最后机械修复后需再次执行全仓 Ruff；strict mypy 109 files、Alembic 单 head `0034_structured_rubric_authority`、`git diff --check` 已通过。

2026-08-05 后端剩余测试同步（未提交、进行中）：离线 PostgreSQL 0034 现在生成带 `IF EXISTS ... RAISE EXCEPTION` 的 fail-closed 安全 SQL，并保持在线迁移原有锁后计数语义；联考/识别测试不再依赖已删除的 Legacy Rubric 与 manual-publish 路由，processing manifest 只使用 Structured Set blocker。相关迁移边界 8 passed，AI worker 15 passed，数学 stale 6 passed；识别旧路由断言已更新为明确 404，尚待最终聚焦和全量复验。

2026-08-05 后端 AI worker 夹具继续修复（未提交、进行中）：active Set 复用后，学生答案的 `question_version_reference` 需与 SetItem 的规范题目 token 对齐，否则 worker 正确地返回 stale；已在测试夹具显式对齐。AI 创建请求测试已删除客户端传入 `rubric_version_id`，改由服务端 active Set 推导；尚待 AI worker 组复验。

2026-08-05 后端全量失败簇修复（未提交、进行中）：全量首轮 `583 passed, 18 skipped, 27 failed` 的主要原因是旧 head 断言仍停在 0033，以及数学/AI worker 测试夹具在 Structured-only 工作流已自动创建 active Set 后又重复插入同题 version=1 答案。已将 head 断言切到 0034，并让 `validation_fixture` 精确复用 active SetItem 的答案、Structured Rubric 与 criterion，同时把 MathValidationJob 固定到该 Set；尚待相关测试组及全量复验。

2026-08-05 前端格式门禁收尾（未提交）：对异步卸载保护及 Structured-only API/测试改动涉及的 4 个文件执行仓库现有 Prettier 机械格式化；未改变运行逻辑。随后需重新确认 Prettier、ESLint、TypeScript 与 Next build。

2026-08-05 前端异步卸载竞态修复（未提交）：`AssignmentGenerationPanel` 的异步加载在组件卸载后不再写入 React state；成功路径与异常路径均由 mounted guard 保护，避免向导测试 teardown 后出现 `window is not defined` 未处理 rejection。前端全量现为 `27 files / 168 tests passed`、无 Vitest unhandled error；此前未通过的结果仅由该测试清理竞态造成。其余前端静态门禁尚待本轮复验。

2026-08-05 Structured-only 最终 Legacy 运行时审计收尾（未提交）：恢复对账 CLI 的表清单已删除迁移 `0034` 明确移除的 `rubric_versions/question_rubrics/rubric_items`，改为对账唯一权威链 `reference_answer_versions/structured_rubric_versions/rubric_criteria/structured_rubric_sets/structured_rubric_set_items`；前端测试辅助函数同步采用 Structured 命名，便于精确零残留扫描。该修改不读取或转换旧数据、不放宽恢复/发布/评分门禁，尚待恢复安全测试、前端相关测试与全量门禁复验。

2026-08-05 Structured-only 批改主测试组验证（未提交）：`test_exception_versioning.py` 与 `test_submission_workflow.py` 联合全量 `29 passed, 111 warnings`（142.11s），覆盖 active Set 切换后的历史成绩冻结、新提交使用新 Set、活动 Set 固定版本优先于更新 confirmed Rubric 等场景；`test_confirm_results_contract.py` 全量 `17 passed, 105 warnings`，旧快照在 active Set 漂移后不可复用。上述运行均由数据库守卫确认 `ahamark.db` unchanged；相关 Ruff check/format 通过。

2026-08-05 Structured-only 批改固定版本负向契约（未提交）：新增回归用例，在 active StructuredRubricSet 已固定逐题 Rubric 后，再创建同题更高版本且 `confirmed` 的 StructuredRubricVersion；实际批改结果仍必须写入 active Set ID 和 SetItem 固定的旧 StructuredRubricVersion ID，明确不得按“最新 confirmed”漂移。该用例 `1 passed`，Ruff check/format 通过，`ahamark.db` unchanged。

2026-08-05 Structured-only 异常/版本回归重建（进行中、未提交）：`test_exception_versioning.py` 已移除最后一个 Legacy `RubricVersion` import 和已删除的逐题 Rubric PUT 接口。原场景现通过创建新的不可变 active StructuredRubricSet（新的答案与 Structured Rubric 版本）表达评分权威变化，并明确断言已发布旧答案、旧评分结果和两版成绩快照保持原状态且继续引用旧 Set；后续新提交才使用新的 active Set。尚待该文件实跑。

2026-08-05 Structured-only confirm-results 契约重建（进行中、未提交）：`tests/test_confirm_results_contract.py` 的人工构造结果已改为同时固定 `structured_rubric_set_id` 与逐题 `structured_rubric_version_id`；原“切换 Legacy active rubric version 后旧快照不可复用”用例改为创建并切换全新的 active StructuredRubricSet（含新的答案/Rubric 版本），继续验证旧确认快照必须失效且正式数据计数不变。共享 Structured fixture 支持显式 set/answer/rubric 版本号，便于表达不可变 manifest 的版本漂移；尚待该文件实跑及补充“更新 confirmed Rubric 不得越过 active Set”反例。

## 接手必读：当前状态、问题与下一步

- 2026-08-05：前端剩余 Structured-only 已切换：作业向导第 5 步删除手动 Legacy Rubric/标准答案快捷录入，生成审核删除 `savedRubrics` Legacy prop 与旧已保存 fallback；批改复核统一使用 `structured_rubric_set_id`、`structured_rubric_version_id`、`criterion_id`；数学验证与 AI 建议显示 Set/Structured 版本，AI 创建请求不再接收或发送任意 `rubric_version_id`，由服务器 active Set 推导。后端 Math/AI job JSON 同步输出 `structured_rubric_version_id`。验证：TypeScript 通过；相关 4 files / 53 tests 通过。前端全量、Prettier、ESLint、Next build 尚待运行。

2026-08-05 结果导出测试契约同步（进行中、未提交）：`SnapshotPayload` 测试夹具已从 `rubric_version_id` 改为 `structured_rubric_set_id`，保持指标、XLSX/PDF 只读取不可变成绩快照的边界，不引入任何 Legacy 回退；结果文件现 `8 passed`。

2026-08-05 全仓 collection 在上述 processing 测试切换后已越过这两个文件，收集到 620 tests；当前唯一 collection blocker 转为 `tests/test_exception_versioning.py` 仍导入已删除的 Legacy `RubricVersion`，因此不能声称全仓 collection 已通过。

2026-08-05 processing 输入与 PostgreSQL 并发测试已移除 Legacy fixture（未提交）：`test_processing_input_snapshot.py` 的固定正式内容测试改为由 StructuredRubricSetItem 精确选择答案/Rubric，不会因后来草稿或 retired 版本改变；只读构建器契约明确要求 Structured Set validator，并固定缺失/漂移错误为 `STRUCTURED_SET_REQUIRED/STRUCTURED_SET_STALE`。PostgreSQL drift matrix 通过篡改 SetItem criteria hash 验证 stale，不再修改 projection profile。`test_processing_orchestrator_concurrency.py` 的 Codex apply fixture 改为 active Set/SetItem，请求携带 Set 与三个内容 hash；删除 generation/review/binding/Legacy rubric tree。两文件 collection 共 20 tests；当前环境实跑 `9 passed, 11 skipped`，11 个 skip 均为缺少显式隔离 PostgreSQL URL 的条件型测试，Ruff 通过，`ahamark.db` unchanged。

2026-08-05 作业路由回归同步（进行中、未提交）：旧测试不再通过已删除的逐题 Rubric PUT 和 manual-publish 旁路构造发布，改为明确断言三条旧路由均为 404，唯一 `/publish` 必须携带服务器生成的 Structured Set readiness；完整一次发布事务由中央发布专项覆盖。作业路由文件现 `10 passed`。

2026-08-05 题目版本 token 一致性补齐（进行中、未提交）：除教师手动 Structured Rubric 创建外，生成候选物化正式 Rubric 也已改用共享 UTC `question_version_token`，避免 SQLite/跨方言时区表示差异让刚创建的 Set 被误判 stale；对应物化→确认→Set 回归已通过 `1 passed`。

2026-08-05 合成 seed 的 Structured Set 版本与内容指纹复用生产端 question-version token 及 semantic payload/hash；不会用任意测试 hash 冒充当前 Set，运行时能够按与发布链一致的规则复核。

2026-08-05 三个合成 seed 已切换为 Structured-only（未提交）：`seed_analytics_demo.py` 与 `seed_capacity_results.py` 现在为每题创建明确的 confirmed ReferenceAnswerVersion、StructuredRubricVersion、manual-only RubricCriterion，以及 assignment-level active StructuredRubricSet/SetItem manifest；成绩快照写 `structured_rubric_set_id`，作业写 `active_structured_rubric_set_id`。`seed_recovery_fixture.py` 不再创建 QuestionRubric/RubricItem 或修改已发布评分标准，而是验证容量 fixture 的活动 Set、精确 SetItem、Structured Rubric 与 criterion 链，并让恢复边界快照引用同一 Set。新增 AST 防回归测试禁止这三个 seed 导入/构造 Legacy 三模型、写旧作业活动字段或向成绩快照写旧 rubric FK。Ruff、三个模块直接 import、恢复安全与 seed 契约 `18 passed`，`ahamark.db` unchanged；未使用真实数据。范围外扫描发现 `scripts/verify_score_correctness.py` 仍直接构造 Legacy RubricVersion/active_rubric_version_id/旧快照 FK，`scripts/business_browser_e2e.mjs` 仍读取旧结果/快照 `rubric_version_id` 并把 criterion key 当作 rubricItemId；按本子任务边界只记录，未扩散修改。

2026-08-05 Structured-only grading 测试重建（进行中、未提交）：`tests/test_grading.py` 已删除 Legacy result/snapshot 字段、`rubric_item_id/rubric_items` Provider payload 和旧质量队列断言，改为断言 Set ID、逐题 StructuredRubricVersion ID、`criterion_id/rubric_criteria`；新增旧 `rubric_item_id` Provider Schema 必须 ValidationError 的负向契约，该文件 Ruff check/format 与全量 `19 passed`。新增共享 `structured_rubric_support.py`，直接创建 confirmed ReferenceAnswer/StructuredRubric/criteria 与 active Set/SetItem，并按正式发布相同 payload 计算三类 hash，不再经 Legacy API/projection。`test_submission_workflow.py` 的主 fixture、手工结果、criterion 和 snapshot 已切到 Set；首段 14 项通过后暴露并修复测试 Provider 仍读取 `rubric_items`，后段含该回归的 9 项通过，合计覆盖该文件 23 项，`ahamark.db` unchanged。confirm-results 大型 fixture 仍待切换。

2026-08-05 Structured-only 中央发布回归重建（进行中、未提交）：删除原测试中所有 Legacy binding/projection 操作，改为验证 Set 是唯一发布权威、readiness 不含 binding、最终一次发布激活 Set，以及 criterion 内容漂移必然 409。为消除 SQLite 丢失时区而造成的伪漂移，题目版本 token 统一按 UTC 规范化，Structured Rubric 创建、Set 校验和处理输入共用同一函数；真实题目更新时间变化仍判 stale。中央发布文件现 `11 passed`，`ahamark.db` unchanged。

- 2026-08-05：Codex local Structured-only 批改契约已完成模块切换（未提交）：`processing/codex_local.py` 的 work request、claim/apply 锁定与幂等回放现在只接受活动 `StructuredRubricSet → StructuredRubricSetItem → ReferenceAnswerVersion/StructuredRubricVersion → RubricCriterion` 固定链；请求携带 Set/Item ID 及答案、Rubric、criteria hash，apply 在写入前重新校验 owner/assignment/paper/活动指针、逐题关系和全部 hash，任何漂移以明确 409 fail closed。`GradingJob/GradingResult` 写 `structured_rubric_set_id` 与 `structured_rubric_version_id`，criterion 结果直接写 `criterion_id`；已删除 Legacy publication binding、QuestionRubric/RubricItem mapping 和旧字段读写。AI 仍只生成 `suggested` 且强制教师复核，不会自动确认、发布或写最终成绩。两组夹具已改为真实 Set manifest，work request 不再暴露 `legacy_binding`。验证：`test_codex_local_apply.py` 9 passed、`test_codex_local_work_items.py` 8 passed，相关 Ruff 全通过，`git diff --check` 通过，`ahamark.db` unchanged；尚未据此声称后端全量或 PostgreSQL E2E 通过。

- 2026-08-05：AI/数学验证 active Set 固定性切换已完成：`MathValidationJob` 与 `AIScoringJob` 模型及新迁移 `0034_structured_rubric_authority.py` 新增不可空 `structured_rubric_set_id`（`RESTRICT` FK + index）；新增共享 authority resolver，以已发布且仍有效的 assignment active Set/SetItem 为唯一答案、Rubric、criteria 来源。两个创建 API 已删除客户端 `rubric_version_id`，旧字段作为 extra 会被 422 拒绝；幂等复用、criterion retry、数学 worker 执行前/落库前、AI worker 执行前/落库前均重验 Set 固定关系，Set 漂移时 fail closed 为 stale/409。Codex-local 严格子任务同步固定其已验证的 Set。0034 会拒绝含未固定 Math/AI job 的旧库，避免不可空 Set FK 伪回填；迁移边界 fixture 已补两个任务表，并断言 upgrade/downgrade 时 Set FK 精确出现/移除。新增直接契约测试覆盖拒绝任意 Rubric、Set/答案/Rubric 三元固定和两类旧异步任务 fail-closed。验证：相关 Ruff 通过；AI/数学现有聚焦组 `20 passed`；新增契约 + 0034 迁移边界 `9 passed`。另有旧 worker 测试在收集时因并行 Structured-only 删除 `QuestionRubric` 而失败，属于共享分支待同步的 Legacy 测试 fixture，不是本次执行逻辑失败。未修改任何其他迁移。

2026-08-05 Structured-only 作业 API 切换（进行中、未提交）：`assignments.py` 已删除 `RubricVersion/QuestionRubric/RubricItem` imports、Legacy 详情子树、`PUT /assignments/{id}/rubrics/{question}` 旧写接口及 `manual-publish-readiness/manual-publish` 发布旁路；作业详情改为返回活动 `StructuredRubricSet` manifest 与逐题固定 criterion，`publish_issues` 直接校验 Set 所属 assignment/owner/paper、中央核查 session、完整指纹、题目全集、固定答案/Rubric 关系、分值及教师确认状态，缺失或漂移均 fail closed。作业复制不再复用已发布 Set：它为新题目创建新的 ReferenceAnswerVersion、StructuredRubricVersion、RubricCriterion 和独立 draft Set，原 Set 保持不可变。该模块 Ruff check/format、直接 import 及“旧三条路由不存在、统一 publish 路由仍存在”的运行时路由契约检查通过；现有 `test_assignment_central_review_publish.py` 仍引用中央核查刚删除的 `PROJECTION_WRITE_LOCK_ORDER`，`test_assignments.py` 的全应用收集仍被尚在并行切换的 `processing/codex_local.py` 导入已删除 binding 模型阻断，因此两组不能记为通过，待对应代理完成后重跑。没有自动确认、发布或写成绩。

2026-08-05 Structured Set 发布后固定版本保护（进行中、未提交）：Set 校验已区分“发布前必须仍是当前选择”和“发布后必须复现已激活 manifest”。处理输入不会因后来创建的新草稿 Rubric 偷换版本或错误判定 active Set 过期，而是直接重验 SetItem 固定的题目版本、答案、Rubric、criteria 及三个 hash；真实题目内容版本变化仍明确返回 stale。

2026-08-05 Structured-only 批改切换（已完成模块切换、未提交）：`grading.py` 已移除 Legacy `RubricVersion/QuestionRubric/RubricItem` import、字段、选择与输出路径，统一通过 active `StructuredRubricSet → SetItem → ReferenceAnswerVersion/StructuredRubricVersion → RubricCriterion` 解析器取得固定权威内容；解析器校验作业/所有者/paper、Set 激活状态、逐题绑定、答案与 Rubric confirmed 状态、question version、两级总分，并按发布 Set 相同 canonical payload 重算答案、Rubric 和 criteria 三类 hash，任一缺失、交叉引用或内容改写均以 `STRUCTURED_SET_REQUIRED/INCOMPLETE/STALE` fail closed。直接评分、教师复核、批量确认结果、最终提交、质量一致性和读取 API 均使用同一 Set/逐题 Structured version；`GradingJob/GradingResult` 写 Set ID 与 StructuredRubricVersion ID，criterion result 直接写 `criterion_id`，两条成绩快照路径写 Set ID。评分 Provider Schema 与上下文同步只接受 `criterion_id/rubric_criteria`，旧 `rubric_item_id` Schema 验证失败，不再存在 Legacy Provider 字段。两个模块直接 import、Ruff check/format、显式 mypy 通过；稳定 objective/provider 聚焦 `8 passed`，额外 Schema 探针确认旧字段 fail closed，静态扫描确认两个模块 Legacy runtime token 为 0，`ahamark.db` unchanged。现有包含 Legacy fixture/字段断言的整组 grading 测试尚待统一改造后才能作为新架构全量证据。

2026-08-05 Structured-only 成绩读取切换（进行中、未提交）：`FinalScoreService` 的完整与已发布成绩快照 schema 已由 Legacy `rubric_version_id` 改为 `structured_rubric_set_id`，并校验 Set 所属 assignment/paper 及 manifest 题目全集；正常读取还要求 Set 仍为 active。没有从“最新 Rubric”推断或回退，也未写成绩。

2026-08-05 Structured-only processing 门禁同步（进行中、未提交）：processing orchestrator 的 readiness blockers 已删除 `ACTIVE_CONFIRMED_FORMAL_REQUIRED` 与两个 `LEGACY_PROJECTION_*`，只接受 input snapshot 当前会产生的 `STRUCTURED_SET_REQUIRED/STALE`；这不放宽门禁，Set 缺失或任一固定内容 hash 漂移仍 fail closed。

2026-08-05 Structured-only 前端发布主链切换（已完成、未提交）：前端 session、readiness 和 review bundle 已改为直接使用 `structured_rubric_set_id` / `structured_rubric_set`；删除 publication binding、Structured→Legacy projection/loss report 的类型与 create/get/confirm API。中央核查不再生成、确认或展示兼容版本，常规流程改为在确认项和真实 blocker 处理完成后自动建立不可变 Structured Rubric Set，再进入统一 publication prepare；发布按钮仍只在服务端 Bundle、Set 和 readiness 都为当前状态时开放，最终仍由教师一次“确认并发布”，原有 409 clean/dirty 保护和有界轮询保持。旧 Legacy blocker 文案已删除，新增 Set 缺失/漂移的“重新准备”文案；中央核查测试已删除旧兼容转换用例并改为直接覆盖 Set 自动准备、失败后重扫、卸载迟到响应隔离，以及 missing/stale/server-declared drift 即使 Bundle 误报 ready 也 fail closed。聚焦测试 `1 file / 49 tests passed`，完整前端 TypeScript 检查通过，四个改动前端文件 Prettier check 通过，目标运行时与测试文件中旧 binding/projection/Legacy 标识扫描无命中，`git diff --check` 通过。本轮未运行前端全量、ESLint 或 Next build，不能沿用更早结果冒充本轮验证。

2026-08-05 Structured-only 权威格式改造（进行中、未提交）：用户明确授权仅对 `0006_submissions_grading_review.py` 做最小历史迁移例外。原因是该迁移原先导入当前 ORM `Base.metadata`，一旦 Structured-only 最终模型删除 Legacy Rubric 外键，fresh 数据库会在到达新迁移前由 `0006` 提前创建未来结构并失败。现已把 `0006` 固化为首次入库提交 `f7783f0073592140c1400d6e7f41ffb17638c64e` 的 14 张原始表定义；原始 ORM 与固定定义编译出的完整 PostgreSQL DDL SHA-256 均为 `7b51a51adb536dcda9934e9332b9221c3471e730e2a9d31989bafb06bc0a5681`，SQLite DDL SHA-256 均为 `bcfa404a77ba05597bb4545febea60997704fa2f518e3c7fc6baccf6d586b50a`，独立 hash/SQLite 往返回归 `2 passed`。没有带入后续字段、约束或索引，没有 server default，也未修改其他旧迁移。远端与 Alembic head 复核无漂移后，新增 `0034_structured_rubric_authority`：创建不可变 `StructuredRubricSet/Item`，准备把作业活动版本、发布 readiness、评分结果和成绩快照切到 Structured 外键，并删除 Legacy 三表与 publication binding；迁移在发现 Legacy 正式行时 fail closed，避免静默删除未知数据。中央核查已移除 Structured→Legacy projection/loss report/显式 binding 确认，改为按答案、Structured Rubric、criteria、题目版本、分值和来源指纹形成幂等 assignment-level Set；review bundle 已直接返回 Set 状态，旧 binding API 已替换为 Set prepare/read。处理输入快照也不再选择“最新 confirmed Rubric”或验证 Legacy projection，而是从作业 active Set 的精确 SetItem 取得固定答案、Rubric、criteria，并重新核对三类内容 hash；任一跨作业、缺项或漂移都以 `STRUCTURED_SET_REQUIRED/STALE` fail closed。独立迁移审查发现的 `0034` blocker 已修复：downgrade 精确复用 `0003` 原始 `versionstatus` 六个值，在 PostgreSQL 使用 `create_type=False` 复用仍由 `paper_versions` 持有的现存枚举；upgrade/downgrade 均要求 review session 为空，避免把 binding hash/status 冒充 Set 语义；PostgreSQL 在计数前一次性取得相关表 `ACCESS EXCLUSIVE` 锁并持有到事务结束，关闭检查与破坏性 DDL 间的并发写窗口。所有 batch 新增/恢复外键均显式命名，SQLite `upgrade → downgrade → upgrade`、双向 review-session fail-closed、PostgreSQL 先锁后查及原始枚举契约共 `5 passed`，Ruff check/format 通过；fresh PostgreSQL 完整边界仍待隔离环境实跑。批改、成绩快照、前端和完整门禁仍在切换中。

2026-08-05 第 5 步题目卡片横向布局（已完成、未提交）：按教师截图反馈，将“题目与风险”每题卡片改为左侧题号/答案与评分标准状态、右侧组合确认按钮；桌面端第一列由 15rem 调整为 24rem，按钮保持单行且不挤压状态文字，窄屏仍安全折行为上下结构。只调整布局，不改变原子确认、风险门禁或发布边界。

2026-08-05 当前本地教师实测作业数据转换（仅独立测试 Compose，未提交）：按教师明确要求，将作业 `ab904b09-9bbd-4fbb-a453-3e7936dde7f8` 当前 Bundle 五道正式题的既有 legacy 评分项转换为五份 Structured Rubric 草稿，共 11 个 criterion，各题分值均无损保持 20 分并绑定已有已确认参考答案。所有 criterion 采用 `manual_only`，保留旧标题、说明、分值及旧记录 ID，不臆造 deterministic 自动判分规则；五份 Rubric 均保持 `draft`，没有代替教师确认、发布或写成绩。转换前发现同一 paper 内另有一条无答案/无分值的旧占位题，因不在当前 Bundle 中已明确排除。转换后只读复核确认五题答案绑定均匹配、criterion 数为 1/2/2/2/3，当前剩余 Rubric blocker 仅表示等待教师在第 5 步逐题点击组合确认。该数据写入仅存在于本地 `ahamark-user-test-ac7ceb6` PostgreSQL 卷，不是迁移、fixture 或真实 Provider 质量证据。

2026-08-05 教师实测第 5 步确认入口位置调整（已完成并更新运行实例，未提交）：按教师要求，“题目与风险”中每道题卡片下方新增唯一的“确认题目、答案和评分标准”组合按钮；原先分散在右侧答案和评分标准详情中的普通及较新版本独立确认按钮均从正常路径移除，详情仍用于查看证据、内容与异常处理。为避免前端串行调用两个会各自提交事务的旧接口而产生部分确认，新增 assignment/question 级原子确认接口：请求必须显式确认并携带当前 Bundle、题目、答案和评分标准内容哈希及精确版本 ID；后端按 assignment→question→reference→rubric→criteria 锁序取得权威行，锁后重新计算 Bundle 并校验当前 materialized/selected 版本、答案与 Rubric 绑定、来源、总分和完整 Rubric 规则，全部通过才在同一事务确认两者，任一失败零写入；同一版本已全部确认时幂等返回。既有答案/Rubric 修改及 Rubric 确认入口也在状态检查前锁定并刷新正式行，不能在组合确认的并发窗口覆盖已确认内容。前端使用同步互斥防双击，409 在无本地编辑时沿用自动刷新并要求再次点击，有未保存编辑时禁用按钮并保留内容；缺少完整正式答案/Rubric 或绑定不一致时按钮禁用并给出原因，不把 legacy“已保存”冒充“已确认”，不自动发布。后端完整中央核查发布文件 `11 passed`，覆盖错误指纹零写入、原子成功和重复请求幂等，最终锁序/原子聚焦 `2 passed`；前端专项 `8 passed`、全量 `27 files / 199 tests passed`；Prettier、ESLint、TypeScript、改动 Python 文件 Ruff、仓库标准 strict mypy（107 files）和 Docker Next production build（19 个静态页面）均通过。最终独立复审无 P1/P2 阻断。当前用户测试 Compose 的 Web/API 已按最终代码重建且依赖服务保持原卷；本轮未新增迁移，未暂存、提交、推送或发布。

2026-08-05 教师实测第 4 步题目字段未回填：后端作业详情已包含题号、分值、题目内容和知识点，但前端右侧始终显示空白“添加题目”表单，选择已有题目只切换页面区域，导致教师误以为生成内容没有写入。现选择已有题目时会回填全部可编辑字段，主按钮改为“保存题目”并调用更新接口；“新增题目”会明确清空选择和表单，保留原创建能力。知识点逗号输入在保存前统一 trim，避免空格进入知识点名称。提交前复审进一步补齐三项保护：任意后台 reload 保留仍存在的当前题目，脏表单不会被服务端旧值静默覆盖，主动切换或新增时先确认是否放弃；若脏编辑对应题目已被后台移除，则进入明确冲突状态，保留本地内容、禁用保存，并只允许教师显式放弃后重新加载，绝不把旧内容写到第一题；题目创建/保存期间按钮禁用且 handler fail-fast，防止双击生成重复题。专项测试扩展为 `13 passed`，覆盖回填保存、第 2 题脏编辑经历 reload 后保持、后台移除冲突和重复提交互斥；改动文件 Prettier、ESLint、前端完整 TypeScript 检查与最终 Docker Next production build（19 个静态页面）均通过。最终独立复审无阻断，本地 Web/API 已按最终代码重建。本轮不改变生成、确认或发布边界。

2026-08-05 新建向导步骤修复（已完成并更新运行实例，未提交）：教师实测从 `/assignments/new` 填写基础信息后会直接进入步骤 4。原因是新建页只跳转到 `/assignments/{id}/edit`，编辑向导首次加载随即采用后端 `completeness.next_step`，没有保留“新建成功后应先上传试卷”的产品意图。现将新建成功路由明确为 `?step=2`，编辑页只接受 1–6 的初始步骤并传给向导；普通直接重进编辑页没有该参数，仍按后端完整度恢复，不会破坏已有作业的续编位置。新增前端回归验证显式步骤 2 展示上传入口且不显示步骤 4 的添加题目区；格式化后的向导专项 `9 passed`，Prettier、ESLint、TypeScript 和 Next production build（19 个静态页面）均通过。独立测试栈的 Web 镜像与容器已重建，浏览器刷新后从新建页创建的下一份作业会进入步骤 2；当前已存在且直接打开的旧编辑 URL 仍按完整度恢复。

2026-08-05 本地启动复核（修复完成、运行中，未提交）：用户要求运行刚推送的 `ac7ceb6483f7248450f8f0c30a3d7c1f540839d2` 后，独立 Compose 项目 `ahamark-user-test-ac7ceb6` 的 Web production build 被 ESLint `prefer-const` 阻断，定位为统一准备轮询中新引入的 timer 先声明后赋值。现仅将 timer 改为同作用域 `const`，保持 3 秒间隔、120 秒总预算、可取消等待及陈旧响应保护语义不变；格式化后的中央核查 `78 passed`，Prettier、ESLint、TypeScript 与 Next production build（19 个静态页面）均通过。fresh API 启动又确认 `.env.example` 的 `ALLOWED_UPLOAD_TYPES` 仍使用 CSV，而 Pydantic Settings 会在字段 validator 前把 list 环境变量按 JSON 解码，导致 Alembic 启动失败；现仅将同一三个 MIME 类型改写为 JSON 数组，并同步本地忽略的 `.env`，不放宽上传类型。修复后 fresh PostgreSQL 已迁移到唯一 head `0033_joint_exam_class_authorization`，Web/API/Worker/PostgreSQL/Redis/MinIO 六服务均 healthy，`GET /health` 返回 ok、`GET /ready` 返回 available、Web 返回 HTTP 200，浏览器已打开 `http://localhost:3000`。当前 assignment generation Provider 与文字 OCR 按开发安全默认显示 unavailable，均不是硬依赖；不会冒充真实 AI/OCR 质量。本次运行不使用真实数据或密钥；默认 Docker 地址池耗尽时通过临时 override 使用未占用的 `10.250.0.0/24`，没有删除、复用或修改其他 Compose 项目的网络和数据卷。当前本地启动与新建向导修复合计七个 tracked 文件仅保留为未暂存变更，等待用户试验反馈后再决定是否提交。

2026-08-05 “教师正常流程一步到位”专项（最终独立复审通过，已授权精确提交）：从远端 Draft PR #1 head `9f6aedfdc3a843e102b48c2f9b1ae573b6ba6b01` 的系统独立 worktree 接手；开始修改前工作区/暂存区干净且为 detached HEAD。最终独立复审确认没有 P1/P2；最近一次主代理远端只读复核时 `codex/grading-confirm-results` 仍为同一 head，PR #1 仍为 open/Draft/mergeable、base `master`，未合并。用户已授权仅对本专项 16 个已知文件精确暂存、提交并推送；本 worktree 只负责本地提交，推送交由已确认 `mwmd0629` keyring 与 `repo` scope 的 Windows 用户上下文完成。本轮没有新增或修改迁移、数据库、lockfile、Docker、真实答案/成绩或已发布状态；授权不包含合并、部署或改变 PR Draft 状态。

2026-08-05 独立提交前复核阻断（已修复，最终复审通过）：复核确认 worker 的 `generating_rubrics` 统一 Provider 分支原先只允许 `openai_compatible`，导致 Fake 落回旧 `generate_candidates(..., True)` 直接写候选，先前关于 Fake 已统一经过 `generate`、Schema 和 invocation audit 的结论不成立。现将 Fake 仅在答案/Rubric 阶段接入统一 `dispatch_stage`，每题分别调用 `answer_generation`、`rubric_generation` 并通过 `_record_invocation` 留存审计；processing_pages 恢复只允许 OpenAI-compatible，旧 helper 的 worker 调用固定为不可用模式，不能再直接生成 Fake 候选。新增 worker E2E 将旧 helper monkeypatch 为抛错，验证两个 Fake invocation 的 provider、endpoint mode、request/response hash、model snapshot 及候选 provenance。最终按钮移除旧 aria 兼容名，可访问名称和测试断言均统一为“确认并发布”。新增 E2E 首次运行因夹具非法跳过中间状态被既有状态机正确拒绝；夹具随后补齐合法转换，未放宽产品状态机。Ruff/Prettier 仅机械统一补丁引入的格式和行尾。该轮当时按复核要求只修复和验证，现已纳入最终复审通过及精确提交授权范围。

2026-08-05 第二次提交前复核 P1（已修复，最终复审通过）：Provider 的 `route_scoring_mode` 原先忽略 `requested_scoring_mode`，会把 `manual_only`、`hybrid`、`ai_suggestion` 的明确降级输出静默升级为 deterministic；前端也会丢弃统一准备接口的 `preparing` / `exception_required` HTTP 200 响应，并由单次 attempt 锁住同一 Bundle。Provider Schema 现要求所有非 deterministic 输出必须给出非空 `degradation_reason`，deterministic 输出禁止自相矛盾的降级声明；语义路由保留 Provider 请求的降级模式，且即使绕过 Schema 也 fail closed，不会升级；`manual_only` / `hybrid` 同时保留既有 `MANUAL_RUBRIC_REQUIRED` 告警。前端现对 `preparing` 最多轮询三次，展示服务端阶段和进度；`exception_required` 的服务端消息（包括 binding 异常）进入定向可见阻断。Bundle 指纹变化、人工重扫、作业切换及卸载都会失效旧准备请求并清空旧异常；准备 effect 在刷新/busy、自动化或 Bundle 错误期间暂停，人工重扫完成后再重试，不会抢跑旧 Bundle、无限循环或让陈旧响应污染当前页面。新增前端回归覆盖直接 ready、preparing→ready、binding 异常、输入变化后的旧响应及卸载停止轮询。

本次 P1 修复的真实验证：后端新矩阵 `8 passed, 21 deselected`；Provider/worker 三文件扩展组首次 `50 passed / 1 failed`，修复 Jordan 手工题兼容告警后整组重跑 `51 passed`（`177.74s`）；前端中央核查前两次均 `76 passed / 1 failed` 并分别暴露夹具竞态和真实进度被旧 blocker 遮挡，修复后最终重跑 `1 file / 77 tests passed`。最终两个前端文件 Prettier check、TypeScript、两个 Python 文件 Ruff check/format-check 及 `git diff --check` 均通过；Alembic 仍为唯一 head `0033_joint_exam_class_authorization`，迁移和 lockfile 无 diff，暂存区为空，根目录及 API 目录的 `ahamark.db` 均不存在。本轮失败 pnpm 命令产生的 worktree 根 `.pnpm-store` 已在用户明确授权后经绝对路径、父目录和目录名三重核对精确删除，没有触及其他路径。主代理额外聚焦回归在 120 秒超时前显示 31 个点且无失败，但进程超时，因此不能计为通过或沿用为本轮验证结论。

2026-08-05 第三次提交前复核 P1（已修复，最终复审通过）：第二次修复的统一准备轮询只有 `3 × 50ms`，真实观察窗口约 100ms，耗尽后同一 Bundle 的 attempt key 又会阻止自动继续，不适合分钟级异步 worker；同时后端 `job.progress` 为 0–100，前端错误乘以 100 会把 `55` 显示为 `5500%`。现改为每 3 秒检查、总 elapsed-time 预算 120 秒的可取消轮询；Bundle 变化、人工重扫、作业切换和卸载会立即解除旧 timer 并使迟到响应失效。预算耗尽后保留最后阶段和进度，明确提示“系统仍在准备、可重新扫描”，发布按钮继续安全禁用。进度统一直接 clamp/round 到 0–100。前端回归使用 fake timers 覆盖多次 preparing 后 ready、`55 → 55%`、完整两分钟预算耗尽及卸载清除 timer，不产生真实分钟等待；格式化前后中央核查文件均为 `1 file / 78 tests passed`，最终两个改动前端文件 Prettier check 与 TypeScript 均通过。`git diff --check` 通过；Alembic 仍为唯一 head `0033_joint_exam_class_authorization`，迁移和 lockfile 无 diff，根/API `ahamark.db` 与 `.pnpm-store` 均不存在，暂存区为空。不改后端 Provider；本轮未重跑后端测试，也不沿用此前结果冒充本轮结果。

Provider 契约现要求答案/Rubric 顶层及每项 criterion 显式给出 evidence 与 `degradation_reason`（正常明确为 `null`），并保留题目分值、criteria、validation rule、依赖和置信度；deterministic 输出在 Pydantic Schema/语义入口强制要求总分及顶层和 criterion 的受支持 `answer_type`，空规则或 manual/未知规则直接 fail closed。测试 Fake 改为实现并调用同一个 `AssignmentGenerationProvider.generate`，经过同一响应 Schema、request/response hash 与 invocation audit 后才物化，仍仅在 test 环境启用并标记 `deterministic-test-only`，不冒充真实 Provider；production fake 禁用、external requests 默认关闭、prompt injection 与成本/网络边界保持不变。候选自动采用资格新增 Rubric 置信度/证据、降级原因和最新 deterministic verified validation 的权威检查，未知来源、低置信、缺证据、manual、非 deterministic、indeterminate、分值或依赖冲突继续定向阻断。

新增 assignment 级幂等 `prepare-publication` 编排，统一恢复/推进异步生成状态、只自动物化服务端权威合格候选、建立无损 binding 并生成 readiness snapshot，可返回 `preparing`、`exception_required` 或 `ready`。系统准备的正式答案/Rubric 保持 draft，以 `system_prepared` 和 `teacher_reviewed=false` 审计，不伪造教师复核、不自动发布；有损 projection 仍进入定向异常。现有 readiness snapshot 作为 Publication Bundle，哈希覆盖作业状态、来源快照、生成/修订版本、风险和 binding；最终 `teacher_publish` 在同一事务锁内重查指纹、版本、门禁和 projection 后，才把 Bundle 内安全草稿整体确认为正式内容并发布，最终动作仍仅限授权主教师。前端正常路径展示自动准备状态、Bundle 摘要及唯一可见的“确认并发布”，移除 `window.confirm`；来源/分值/manual/不确定校验/有损映射保留异常处理。发布 409 在无本地编辑时有界重新 prepare/reload/retry；答案/Rubric 编辑器对 clean 409 自动重载，对 dirty 409 保留编辑并提示教师决策。

上一轮 Fake worker 旁路复核的实际验证：新增防旁路专项 `1 passed`；格式化后的 Provider/OpenAI 与 worker 聚焦组 `22 passed`，`ahamark.db` 守卫 unchanged；格式化后的中央核查前端 `1 file / 72 tests passed`。两个改动 Python 文件的 Ruff check 与 format-check、两个改动前端文件的 Prettier check、TypeScript 和 `git diff --check` 均通过。Alembic 仍为唯一 head `0033_joint_exam_class_authorization`，迁移目录无 diff，根/Web lockfile 无 diff，Web lockfile与 `ahamark.db` 不存在，暂存区为空。该轮没有重跑后端全量、前端全量、Next build、strict mypy 或 browser E2E，不能沿用更早结果冒充该轮结果。

剩余风险 / 下一位接手：最终独立复审已确认本次 Fake worker 路由、防旁路测试、Provider 降级路由与前端准备状态机没有 P1/P2；如需恢复完整交付结论，仍须重新运行修复后的后端/前端全量和相应静态门禁。本轮未使用 Docker 环境，也未启动 fresh PostgreSQL/browser 环境；后续 browser E2E 必须分别标记 Fake、Codex 合成 fixture 与真实 Provider，不得冒充真实 Provider 质量或放宽安全门禁。pnpm 临时缓存已清理，无额外未跟踪任务产物。用户已授权精确暂存、提交并推送这 16 个产品/测试/账本文件；本 worktree 完成本地提交后，由已认证的 Windows 用户上下文执行非 force 推送并复核远端与 PR。仍禁止自动合并、部署或把 Draft PR 改为 Ready。

2026-08-04 教师实测“点击后没反应”改进专项：从远端 Draft PR #1 head `609673089c0e8e1f9fb35cd815e0cf7b3bbb80f9` 创建干净隔离 worktree；接手时 PR 仍为 Draft/open、base `master`，无评论、审查或检查更新，本轮没有改动正在运行的测试数据库、Docker、正式答案/成绩或发布状态。实测接口证据表明，“批量接受可用评分标准”实际 HTTP 200 但 `accepted_count=0`，旧响应不报告考虑了哪些候选及为何跳过，前端又无条件显示成功；五份合成评分标准的 criterion `validation_rule={}`，因此均被 `RUBRIC_VALIDATION_CONFIG_INVALID` 阻断。同时中央核查在正式答案/评分标准未完成时已允许点击“生成兼容版本”，后端只能返回 422，形成第二个看似无反应的入口。这里的候选来自明确标记的 `codex_simulated` 测试资料，不宣称是真实外部 Provider 输出质量。

本轮后端为答案与评分标准候选返回服务端权威的 `server_eligible` 和 `ineligibility_reasons`；两个批量接口统一返回 accepted/considered/skipped 数量、ID、题号和原因码，只统计仍为 `suggested` 的当前候选，继续保持未知来源、低置信、缺证据、人工复核、结构无效、非 deterministic 或验证 indeterminate 时 fail-closed。前端对零接受和部分接受分别显示明确计数、题号、中文原因与修复动作，不再把“接受 0 项”显示为成功；候选卡片直接解释为何不能自动接受。新增字段按可选字段兼容旧缓存/滚动升级，服务端未明确返回 `false` 时不会使旧页面数据崩溃。中央核查只有在非 binding blocker 清零且正式答案、评分标准确认完成后才自动或手动创建兼容 binding；前置条件不满足时按钮禁用并提示先完成哪些内容。AI 仍仅为 suggestion-only，未增加任何自动教师确认、正式成绩写入或发布路径。

最终验证（2026-08-05）：后端全量使用唯一系统临时目录 `C:\\Users\\Lenovo\\AppData\\Local\\Temp\\ahamark-provider-feedback-full-20260804-01` 且禁用 cacheprovider，结果 `579 passed, 18 skipped, 339 warnings`，无失败或 setup error，耗时 `44:49`，`ahamark.db` 守卫 unchanged；18 个 skip 均为仓库既有条件型用例。答案/评分标准生成专项 `18 passed`；前端相关组件 `78 passed`，前端全量 `27 files / 186 tests passed`；Prettier、ESLint、TypeScript、Next production build（19 个静态页面）、全仓 Ruff check、仓库标准 strict mypy（107 source files）、Alembic 单一 head `0033_joint_exam_class_authorization` 与 `git diff --check` 均通过。Next build 输出仓库既有的 SWC lockfile 自动修补警告，但构建退出码为 0，构建前后根 lockfile 均无 diff，Web lockfile 仍不存在。依赖安装仅用于隔离 worktree 验证；npm 报告既有 4 个 high advisories，未运行会改锁文件的自动修复。未运行 fresh browser E2E，不能把组件回归夸大为浏览器端到端验证；当前 `localhost:3300` 仍是旧测试实例，本轮不部署或替换它。本专项 7 个产品/测试文件加 README 将按用户授权精确暂存、提交并安全推送到现有 Draft PR #1，仍不合并、不部署、不改变 PR 状态。

2026-08-04 独立提交前复审阻断跟进：复核确认旧 release 公开与 confirm-results 原先没有共同事务序列点；现统一先执行受 owner 限制的 assignment 自更新，PostgreSQL 获得行写锁、SQLite 获得写事务锁，且不改变 assignment 内容指纹，锁顺序统一为 assignment → narrower rows。公开在共同锁内重新读取 release 并保留“更高正式版本则 409”的历史审计语义。联考只读入口也已在查询 metadata 前要求 active teacher，撤权后返回 `403 TEACHER_ROLE_REQUIRED`。第二次复审指出原并发回归可能把慢调度误判为锁生效；测试现先确认真实 HTTP confirm 已发起，再监听 pytest 隔离数据库 engine 的 `before_cursor_execute`，确认请求实际发出 assignment serialization SQL 后，才验证 publish 持锁期间 `_confirm_results_state` 不可达。三组证据分别为：

- 单次聚焦回归：`1 passed`。
- 稳定性回归：10 个独立 pytest 进程、10 个独立 basetemp，`10/10 passed`。
- mutation/bypass 反证：一次性 pytest collection hook 绕过共同锁，结果 `1 failed`；失败点为未观测到 assignment serialization SQL（`confirm did not reach the assignment serialization SQL`）。绕锁逻辑只存在于该一次性测试进程，未写入工作区。

相关产品修复扩展专项保持 `88 passed`；完整 confirm-results、学生端和 results 发布专项为 `29 passed`、无 skip/error、耗时 `1:56`。上一轮后端全量 `576 passed, 18 skipped` 仅作为基线，本轮未重跑约 34 分钟全量，不能表述为本轮全量通过。全仓 Ruff check、本轮测试文件 Ruff format-check、strict mypy `107 source files` 与 `git diff --check` 均通过；Alembic 唯一 head 为 `0033_joint_exam_class_authorization`，根 lockfile 和 `0031_student_portal.py` blob 与 HEAD 一致，`ahamark.db` 与 Web lockfile 不存在，暂存区为空。没有产品、前端、模型、迁移或依赖改动；最终 README 澄清轮只修改本账本，未暂存文件总数仍为 13。

2026-08-04 PR #1 独立审查修复专项：本 worktree 从 Draft PR #1 的远端 head `6d0941bd1d4ecb40810f4f6927d7e9f2967083e1` detached 起步；开始修改前已确认工作区/暂存区干净、PR 仍为 Draft 且没有新评论/审查/检查或协作者更新，Alembic 唯一 head 为 `0033_joint_exam_class_authorization`，当时未启动 Docker 验证环境，`ahamark.db` 与 Web lockfile 不存在且根 lockfile 无改动。五项 finding 均先独立复现，再按 fail-closed 边界修复：

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
