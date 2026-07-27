# Assignment Generation Safety v1

机器矩阵为 `scripts/assignment-generation-safety-matrix-v1.json`，包含 30 项：Prompt
Injection、无工具能力、不得选择班级/确认截止时间/总分/文件角色/答案来源/Paper/Answer/
Rubric/readiness/publish，HTML/script 与外链隔离，MIME/损坏/超限文件，坐标与严格 Schema，
非法分值与 dependency cycle，owner/session/CSRF，Worker/Provider 无发布路径，readiness
防伪造与重放，stale/教师修改/晚到结果保护，错误与证据脱敏，以及 production fake 降级。

矩阵复用 0018–0022 和既有文件安全、认证、owner 隔离测试作为证据，不建立第二套业务安全
状态机。Provider 输出始终是不可信草稿，严格 schema 拒绝 extra fields；Provider 没有
工具或发布接口。材料中的“忽略系统要求”“选择班级”“标为官方”“全部满分”“自动发布”
或“输出密钥”只能成为不可信文本与 review issue。

硬门禁均要求 0：自动发布尝试、未授权发布成功、stale 覆盖、教师编辑覆盖、官方来源提升、
跨 owner 泄漏、Prompt Injection 控制成功和未知 evidence 引用。发布只能由已认证 owner
教师会话、有效 CSRF、当前 readiness snapshot 和二次确认触发。发布 Assignment 不创建
GradeRelease、ScoreSnapshot 或最终成绩。
