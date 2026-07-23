# 第四部分：成绩正确性专项

## 结论

验证数据集 `score-correctness.synthetic.invalid/20260723T080000Z` 的服务级对账和真实
Microsoft Edge 关键路径均通过，第四部分可以正式关闭。正式成绩链路只使用 finalized
Submission 上的 complete SubmissionScoreSnapshot；缺交和未完成学生不计零分、不进入统计分母。

本专项修复了 Snapshot 关系校验、最终成绩选择、Release item 关系校验、KnowledgePoint
样本量、趋势发布版本选择和规则型 TeachingInsight 标识。项目整体等级仍为 C；权限、
文件安全、容量、恢复和生产运维不在本专项范围内。

## 金标准数据

- marker：`score-correctness.synthetic.invalid`
- run ID：`20260723T080000Z`
- 6 名纯合成学生：4 名完成、1 名缺交、1 名有 Submission 但未 finalized
- 4 道题，满分 50：2 道客观题、2 道主观题，覆盖满分、零分、部分得分和多知识点
- 教师最终错误类型：`客观题错误`、`主观题人工评分错误`
- v1 成绩：48、18、32、40；v2 仅改分学生由 40 变为 45
- 缺交和未完成学生在 v1/v2 均无正式成绩

独立 golden 数据位于 `tests/fixtures/score_correctness/golden.json`。预期统计由验证脚本的
独立算术逻辑产生，不调用被测指标实现来构造预期值。

## 对账结果

### Snapshot 与 GradeRelease

- v1 固定 4 个 complete Snapshot；v2 为改分学生产生新 Snapshot。
- complete Snapshot 校验 Submission、学生、作业、PaperVersion、RubricVersion、题目元数据、
  TeacherReview、StudentAnswer、KnowledgePoint、分题上下界及顶层合计关系。
- FinalScoreService 跳过非法 complete 候选，按学生选择最新合法 Snapshot。
- Release v1/v2 item 固定具体 Snapshot ID，并再次校验学生、Submission、班级和作业关系。
- 缺交及未 finalized 学生均未进入 Release。

### XLSX 与 PDF

- 实际解析 XLSX：v1/v2 成绩总表均为 4 行，版本说明分别固定相应 Release。
- 学号按文本写入；未完成学生没有被写成零分；外部文本经过公式注入防护。
- 实际解析中文 PDF：v1 显示旧分数，v2 显示新分数；总分、满分、分题、教师评语和错误类型
  来自固定 Snapshot。
- v2 生成后重新读取 v1 输出，v1 值保持不变。

### Analytics、下钻、学生详情与 Insight

- v1：参与 4，平均 34.5，最高 48，最低 18，中位数 36。
- v2：参与 4，平均 35.75，最高 48，最低 18，中位数 38.5。
- 分数段和 A/B/C/D 分层与 golden 一致；客观题给出正确率，主观题 `correct_rate=null`。
- KnowledgePoint 按题目关联累计，多知识点题分别计入；样本数按实际参与学生去重。
- 错误类型只使用 TeacherReview 的最终确认值。
- 分数段、题目和 KnowledgePoint 下钻人数均为 4 人口径内的对应子集。
- 改分学生详情显示 v2 的 45 分；趋势选择每份作业最新有效发布版本。
- TeachingInsight evidence 固定 v1 AnalyticsSnapshot，并明确标记 `rule_based` 与免责声明。

## 历史版本

Release v1、报告 v1、Analytics v1 和 Insight v1 均固定各自来源 ID；产生 v2 后重新对账，
v1 内容未改变。v2 只反映一名学生由 40 到 45 的新成绩，不覆盖旧 Snapshot 或旧 Release。

## 浏览器证据

真实 Microsoft Edge（headless）完成 12 个步骤：

1. 合成教师登录；
2. 选择 Release v1，显示平均分 34.5；
3. 选择 Release v2，显示平均分 35.75 和样本量 4；
4. 90–100 分段下钻显示 2 人；
5. 改分学生详情显示 45；
6. 第 1 题下钻显示 4 人；
7. KnowledgePoint 下钻显示 4 人；
8. 生成并显示规则型 TeachingInsight；
9. 班级趋势仅显示最新有效 Release v2：1 个点、4 人、71.5%；
10. 改分学生趋势仅显示最新有效 Release v2：1 个点、45/50、90.0%；
11. 客观题错误下钻：预期 3、实际 3，全部来自最终 `final_error_type`；
12. 主观题人工评分错误下钻：预期 4、实际 4，全部来自最终 `final_error_type`。

机器证据见 `docs/score-correctness-browser-verification.json`。

## 验证结果

- Ruff format：64 files passed
- Ruff check：passed
- mypy：44 source files passed
- Pytest：53 passed，0 failed，0 skipped，1 个 Starlette/httpx 弃用警告
- ESLint：passed
- TypeScript：passed
- Vitest：12 files、26 tests passed
- Next production build：passed；警告为受限网络下无法自动补写缺失 SWC lockfile 依赖
- Prettier 全库检查：passed；唯一失败文件
  `apps/web/app/(teacher)/grading/[batchId]/page.test.tsx` 仅由项目 formatter 收拢一条断言布局，
  未改变测试语义，目标测试 3 passed
- 报告解析：XLSX/PDF passed
- Edge：12/12 passed

## 未覆盖项与剩余风险

- 中文字体与 PDF 文本可读性已由生成和文本解析验证，未做逐字形视觉审校。
- 权限、安全、容量、恢复与生产部署不属于第四部分，不能据此将项目提升到 B。
