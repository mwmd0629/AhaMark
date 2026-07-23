# 教师核心业务浏览器闭环

验证日期：2026-07-23（Asia/Shanghai）。结果：**A–H 全部通过**。机器证据见 `business-e2e-verification.json`。

## 范围与环境

验证在 Compose project `ahamark-business-e2e` 中进行，使用独立 PostgreSQL、Redis、MinIO 命名卷及 Web/API/MinIO 端口 `3300/8800/9900`。没有复用、覆盖或清理既有开发栈及命名卷。所有业务数据使用本轮唯一 marker `business-e2e-20260722164927325.business-e2e.synthetic.invalid`，教师邮箱使用保留域 `.synthetic.invalid`，学生、图片和成绩均为运行时合成值。

唯一 CLI 前置是幂等创建专用合成教师；其余班级、导入、作业、OCR、批次、评分、发布、报告和分析动作均由无头 Edge 的真实页面完成。浏览器脚本不以 API 调用替代业务步骤。

## A–H 结果

| 阶段 | 结果 | 浏览器证据 |
|---|---|---|
| A 认证 | PASS | 登录表单、受保护工作台、刷新后会话、localStorage 长期凭据检查 |
| B 班级与学生 | PASS | 创建班级、CSV 预览/确认、3 名合成学生、前导零学号 |
| C 作业与试卷 | PASS | 六步向导、关联真实新班级、上传运行时 PNG、PaperPage 可见 |
| D OCR/题目/Rubric | PASS | 启动并轮询纸卷 OCR、查看/修正/确认候选；一主观一客观题、正分值、题区、知识点、两题 Rubric、完整性检查及发布 |
| E 学生作业 | PASS | 创建批次、上传 4 张合成页、按文件名自动匹配两名学生、Submission OCR、StudentAnswer 与页面顺序保存 |
| F 复核/finalize | PASS | 客观题 `objective-rule` 初批并接受；主观题显示 `unavailable`，教师通过 UI 分别手工给 4/3 分；4 项强制复核后 finalize |
| G 发布/报告 | PASS | readiness 为 2 可发布、1 未完成；创建固定 Release；XLSX 与中文个人 PDF Job 完成；页面请求新的 15 分钟签名地址 |
| H Analytics | PASS | 固定 Release 指标、分布、学生详情、知识点下钻、趋势；规则型建议编辑并确认 |

## 成绩与发布对账

- 两份最新 `complete` ScoreSnapshot：`32cfff83-75f5-40e0-a7f5-223ea549add7`（9 分）、`b8a01797-790a-49d8-b1f3-d70f2d6f2295`（8 分）。
- GradeRelease：`c68f7259-3f6a-44dc-8f59-df6833b1e67f`，固定的 Snapshot ID 与上列完全一致。
- XLSX Job `74566c96-098e-47d3-aaa3-14bd797ee022`、中文个人 PDF Job `4f009a35-62b9-4fc5-806b-d45682d72c80` 均为 `completed`，来源为同一 GradeRelease。
- AnalyticsSnapshot `a27a7854-a040-41d3-97b5-6937775573bd` 读取同一 GradeRelease；参与人数 2、平均分 8.5。第三名未完成学生不进入分母，也没有被记为 0 分。
- 正式成绩来自 finalized Submission 的最新 complete Snapshot；GradingResult 和临时 TeacherReview 仅作为复核输入。

## OCR 与主观题边界

本闭环使用 `fake` OCR，且只在 `APP_ENV=test` 的独立 Compose 中启用。页面明确显示其为“非生产工作流测试适配器”。该证据只证明浏览器 UI、任务状态、持久化与业务编排闭环，不证明 RapidOCR 准确率、手写数学、公式 OCR 或 LaTeX 可靠性。

主观题正式 Provider 保持 `unavailable`，production 仍禁止测试 FakeGradingProvider。两份主观题最终分均由教师在三栏复核 UI 中手动输入；没有由 Fake Provider 生成主观题最终分。TeachingInsight 为固定 AnalyticsSnapshot 上的规则型建议，不是大模型深度分析。

## 未覆盖项

本轮不覆盖异常业务矩阵、完整跨教师资源权限矩阵、拆分/合并页面、批量 PDF ZIP、签名 URL 实际过期等待、OCR/报告并发容量、故障恢复、真实 OCR 准确率、学生端或成绩通知。GradeRelease 的 `released` 仅表示教师确认一组固定成绩，不表示已经送达学生。

## 复现

在仓库根目录执行：

```powershell
docker compose -p ahamark-business-e2e -f docker-compose.business-e2e.yml up --build -d
docker compose -p ahamark-business-e2e -f docker-compose.business-e2e.yml exec api python -m app.cli.seed_business_e2e_teacher
node scripts/business_browser_e2e.mjs
```

脚本成功时打印 `BUSINESS_BROWSER_E2E_PASSED ... stages=8` 并覆写脱敏机器证据。运行数据默认保留；不要执行 `docker compose down -v`。
