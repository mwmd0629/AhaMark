# 教师核心业务浏览器闭环

最新验证日期：2026-08-19（Asia/Shanghai）。结果：**本机隔离环境 A–F 全部通过，并在成绩发布前安全停止**。

## 范围与环境

验证使用 Compose project `ahamark-business-e2e`，以及独立 PostgreSQL、Redis、MinIO v2 命名卷和 Web/API/MinIO 端口 `3300/8800/9900`。旧 v1 卷保留，运行和停止均不得使用 `down -v` 或清理未知卷。

所有账号、班级、学生、学号、图片、答案和评分均为运行时合成数据。教师通过用户名 `business-e2e-teacher` 登录；保留域邮箱只作为合成身份标记。脚本只允许 `localhost:3300/8800`，写操作还要求显式设置 `ALLOW_SYNTHETIC_MUTATIONS=1`，不得指向 node2 或生产环境。

本地 Compose 使用 `fake` OCR/公式 Provider 和 Codex-local 合成建议适配器，只验证 UI、接口、状态、持久化、来源标记及安全门禁，不证明真实 OCR、公式、手写或评分准确率。

## A–F 结果

| 阶段             | 结果 | 主要证据                                                                                                                                                        |
| ---------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A 认证           | PASS | 用户名登录、受保护工作台、刷新后会话、localStorage 长期凭据检查                                                                                                 |
| B 班级与学生     | PASS | 创建大学课程班级、CSV 预览/确认、3 名合成学生与前导零学号                                                                                                       |
| C 作业与试卷     | PASS | 当前三步向导、关联新班级、上传运行时 PNG、PaperPage 可见                                                                                                        |
| D 题目与发布门禁 | PASS | 低质量图片阻止自动确认；教师启用手动切题、确认文件用途、创建客观/主观题及 Structured Rubric；发布检查允许非阻断警告且硬阻断为零                                 |
| E 学生提交与识别 | PASS | 两名学生、四张合成页；处理版本 v3；教师在页面明确框选每题区域，区域来源为 `teacher_explicit`；答案识别证据来源为 `system_auto`                                  |
| F 建议与教师复核 | PASS | Codex-local 只产生建议；缺分建议进入唯一“手动评分”入口，正常建议进入“修改分数”；评分表单自动聚焦并即时校验分项合计；4/4 复核完成；complete ScoreSnapshot 已对账 |

主业务证据的 `result` 为 `passed_through_F`，`completed_through` 为 `F`，`grade_release_write_attempted` 为 `false`，作业下 GradeRelease 查询计数为 `0`。G（发布/报告）与 H（分析）刻意不运行。

## 公式不可读专项

`scripts/formula_unreadable_browser_acceptance.mjs` 另行通过以下浏览器闭环：合成教师登录、创建作业和 PNG 上传、公式质量阻断、教师选择不可读原因并确认、重新框选后恢复为人工待确认。脚本不会把不可读候选自动确认为公式。

## 复现

在仓库根目录执行：

```powershell
docker compose -p ahamark-business-e2e -f docker-compose.business-e2e.yml up --build -d
docker compose -p ahamark-business-e2e -f docker-compose.business-e2e.yml exec -T api python -m app.cli.seed_business_e2e_teacher
$env:ALLOW_SYNTHETIC_MUTATIONS = "1"
node scripts/business_browser_e2e.mjs
$env:FORMULA_ACCEPTANCE_EVIDENCE_DIR = "<仓库外证据目录>"
node scripts/formula_unreadable_browser_acceptance.mjs
```

主脚本成功时打印 `BUSINESS_BROWSER_E2E_STOPPED ... stages=6 completed_through=F`。运行结束使用 `docker compose ... stop`；保留独立卷供审计和复查。

## 未覆盖项

本机验收不覆盖 node2 登录与公网链路、真实 Provider 准确率、真实学生资料、移动 Safari、自签名证书体验、生产容量/恢复、成绩发布、通知、报告或学情分析。上述事项需要单独授权和生产安全门禁，不能由本机 fake 结果外推。
