# 第三部分：异常业务与版本一致性（2026-07-23）

本部分在 `ahamark-business-e2e` 隔离六服务栈上实现并验证。所有浏览器数据使用
`business-exceptions.synthetic.invalid` 标记；没有真实教师、学生或作业数据。

## 不可变数据链

`PaperVersion → RubricVersion → StudentAnswer/GradingResult → finalized Submission → complete ScoreSnapshot → GradeRelease → Report/Analytics/Insight`

- 发布前只读取当前 active Rubric；Rubric 修改会复制新版本并使旧结果/答案 stale。
- `ScoreSnapshot` 按 submission 版本递增且 immutable；失败 finalize 只产生 incomplete 记录。
- `GradeReleaseItem` 固定 snapshot/submission ID；报告和分析只能引用 active released release。
- ReportJob 的 failed/expired 终态已通过真实 Edge 验证：教师点击 retry 创建新 queued Job，旧 Job 的 ID、状态、error_code、created_at、Release、学生范围和报告类型保持不变；刷新后新旧任务仍可对账。
- 同一学生存在拆分、合并或额外未完成提交时，就绪检查按该学生最新合法 complete snapshot 选择，不把未完成提交当零分。

## A–H 异常覆盖

| 组别 | 实现与证据 | 状态 |
|---|---|---|
| A OCR 边界 | 空白、低置信度、公式不可用；候选分数保持 null | PASS（API） |
| B 人工匹配 | 多学号歧义必须教师确认；重复确认幂等，换学生冲突 | PASS（API + UI） |
| C 导入 | 预览阶段校验；重复/非法行 409 且零写入 | PASS（API + UI） |
| D 拆分/合并 | owner/class/assignment/batch 校验；页码连续；旧原文件不变 | PASS（真实 Edge bootstrap） |
| E stale/regrade | Rubric 改版使旧答案 stale；接受被禁；regrade 创建当前 Rubric suggested 并清除答案 stale 标记 | PASS（API + UI） |
| F finalize | 缺分、旧 Rubric、stale 或未复核时只能 incomplete，不能伪造 complete | PASS（API + bootstrap Edge） |
| G release/report | 只发布 complete snapshot；历史 release 固定；failed/expired 通过 UI retry 创建新 Job，旧终态不变 | PASS（API + 真实 Edge） |
| H analytics/insight | 分析固定 release；旧版本指标不变；Insight draft/confirmed/superseded/invalid 生命周期 | PASS（API；正常浏览器链已有） |

## 验证层次与限制

- `tests/test_exception_versioning.py`：5 个测试覆盖 OCR、歧义匹配、快照/发布/报告/分析/Insight、Rubric 改版、最新合法快照选择。
- `scripts/business_report_retry_browser_e2e.mjs`：真实 Edge 观察 failed/expired、点击 retry、捕获 201 新 Job、刷新后关联对账；专用 fixture 仅在 `APP_ENV=test` 下工作。
- 后端完整测试此前为 51 passed；Ruff、mypy 通过。前端组件测试 25 passed；Prettier、ESLint、TypeScript 通过；Next build 成功但保留 SWC lockfile 修补警告。
- 正常教师主路径 Edge 证据仍为 `docs/business-e2e-verification.json` 的 8/8；异常 bootstrap 多次 Edge 8/8；failed/expired retry 的专用 Edge 证据为 `docs/business-report-retry-verification.json`。当前 UI 对 expired 任务直接提供 retry，因此过期下载点击项为 not applicable。
- Fake OCR 只证明页面和编排，不证明 RapidOCR、手写 OCR、公式 OCR 或 LaTeX 准确率；Provider unavailable 的主观题必须教师人工评分。
- 没有迁移旧项目 C 的真实业务数据；没有自动升级、删除或重置数据库卷。
