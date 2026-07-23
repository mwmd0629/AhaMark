# 第五部分 5A：权限矩阵验证

验证日期：2026-07-23。起始提交：
`828a494e2d3df598ab7c9223f0e17703b3b553aa`。数据标记：
`security-matrix.synthetic.invalid`。

## 结论

**PASS（矩阵覆盖范围内）**。矩阵包含 27 类资源、29 类操作、783 个资源×操作格；
117 格适用，666 格明确为 `not_applicable`。每个适用格都套用六种身份结果：
owner、Teacher B、未认证、禁用教师、过期/撤销 Session、缺失/错误 CSRF，共
702 个身份结果。自动化同时枚举 FastAPI 全部业务路由，确认除 health、ready、login
外均经过统一 `CurrentActor` 会话/CSRF 边界。

这不是外部渗透测试，也不证明多实例限速、SSO/MFA 或生产部署安全。

## 适用操作矩阵

下表“其余操作”均为 `not_applicable`，不是省略。29 项操作全集由
`tests/test_authorization_matrix.py::OPERATIONS` 固定并由测试校验。

| 资源 | 适用操作 |
|---|---|
| Session/User | get, create, delete |
| Class | list, get, create, update, archive, restore |
| Student | list, get, create, update, delete |
| StudentGroup | list, create, update, delete |
| ImportPreview/ImportJob | get, create, confirm |
| Assignment | list, get, create, update, archive, restore |
| PaperVersion | get, upload |
| PaperPage | get, update, reorder |
| Question | get, create, update, delete, reorder |
| QuestionRegion | get, create, delete |
| RubricVersion | get, update |
| RubricItem | get, update |
| KnowledgePoint | get, create, update |
| RecognitionJob/RecognitionBlock | get, create, retry, recognize, confirm |
| GradingBatch | list, get, create, archive, upload, bulk_accept, regrade |
| Submission | list, get, split, merge, match, recognize, finalize |
| SubmissionPage | get, retry, reorder, split, merge |
| SubmissionRecognitionJob | get, create, retry, recognize |
| StudentAnswer | get, create, update, review |
| GradingJob/GradingResult | get, create, regrade |
| TeacherReview/ScoreRevision | get, create, update, review |
| SubmissionScoreSnapshot | list, get, create, finalize |
| GradeRelease/GradeReleaseItem | list, get, create, readiness, release |
| ReportJob | list, get, create, retry, download, report_create |
| StoredFile | get, upload, metadata, signed_url, download, delete |
| AnalyticsSnapshot | get, create, analytics_create, drilldown |
| TeachingInsight | get, create, update, confirm, regenerate, invalidate |

## 身份结果规则

- Owner：合法状态进入业务处理；非法状态返回 409/410/415/422 等业务错误。
- Teacher B：owned 查询把直接、父子和间接引用统一隐藏为 404；list 首层按 owner
  过滤。Analytics/Release/Report/Student/Insight 的既有 14 项真实 HTTP 证据继续有效。
- 未认证、过期/撤销 Session、禁用教师：401；production 不启用 demo actor。
- 缺失或错误 CSRF：所有已认证写路由在业务处理前返回 403；GET 不要求 CSRF。

本轮真实 HTTP 为 16/16 PASS，包含未认证、两个真实 Cookie Session、缺失/错误
CSRF、Teacher B Class list/get、StoredFile metadata/signed-url/delete，以及签名 URL
到期和重新签发。证据不包含密码、Cookie、CSRF 或签名查询参数。

## 间接引用

owner 查询在 Class、Student、Group、Import、Assignment、Recognition、Submission、
Answer、Release、Report、Analytics、Insight 和 StoredFile 入口执行；创建操作同时校验
Class、Student、Assignment、Release、Snapshot/File 等外键 owner。拆分/合并、重试、
finalize、confirm、report create 与 analytics create 均先解析 owned 父资源，其他教师
的父/子 ID 不会进入状态校验。

## 证据

- 自动化：`tests/test_authorization_matrix.py`
- 真实 HTTP：`scripts/verify_authorization_http.py`
- 机器记录：`authorization-matrix-verification.json`、
  `authorization-http-verification.json`
