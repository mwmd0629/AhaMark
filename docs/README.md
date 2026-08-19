# 文档导航

根目录 `README.md` 的“接手必读”是唯一当前状态账本。本目录保存稳定说明和历史验收证据；旧验收文件只能证明其生成时的状态，不能覆盖 README 中较新的事实。

## 日常开发与运维

- [项目基线](PROJECT-BASELINE.md)：项目基线与开发约定。
- [常规运维](OPERATIONS.md)：常规运行和运维命令。
- [管理员账号运维](ADMIN-ACCOUNT-OPERATIONS.md)：管理员、教师和学生账号运维。
- [备份恢复](BACKUP-RESTORE.md)、[故障恢复](FAILURE-RECOVERY.md)：备份、恢复和故障演练。
- [业务 E2E](BUSINESS-E2E.md)：隔离合成业务验收范围。
- [本地公式服务](LOCAL-FORMULA-PROVIDER.md)：本地公式服务边界。

## 安全与权限

- [数据安全边界](DATA-SECURITY-BOUNDARIES.md)：数据与隐私边界。
- [权限矩阵](AUTHORIZATION-MATRIX.md)：角色和接口权限矩阵。
- [文件安全](FILE-SECURITY.md)、[文件安全验证](FILE-SECURITY-VERIFICATION.md)：文件处理安全。
- [安全审计](SECURITY-AUDIT.md)：安全审计结论。
- [能力陈述](PRODUCT-CAPABILITY-STATEMENTS.md)：可对外陈述和禁止夸大的能力。

## 作业、识别与评分

- `ASSIGNMENT-*`：作业生成、材料分析、题目抽取、答案和 Rubric 流程。
- `FORMULA-REGION-DETECTION-*`：公式区域检测设计与候选方案。
- [分数正确性](SCORE-CORRECTNESS.md)：分数正确性契约。
- [业务异常与版本](BUSINESS-EXCEPTIONS-AND-VERSIONING.md)：异常与版本语义。
- `RECOGNITION-PRIVATE-*`：私有识别评测规范；当前保持暂停，不代表允许处理私有材料。

## 验收与历史证据

- [能力证据矩阵](CAPABILITY-EVIDENCE-MATRIX.md)：能力到证据的索引。
- [最终验收](FINAL-ACCEPTANCE.md)、[预生产就绪](PREPRODUCTION-READINESS.md)：历史阶段验收。
- [性能](PERFORMANCE.md)、[性能容量](PERFORMANCE-CAPACITY.md)：性能与容量记录。
- `*-verification.json`、`*-results.json`：脚本生成的版本化证据快照。
- [历史交接](HANDOFF.md)：历史交接长记录；新任务不要把它当成最新状态账本。

新增文档前先判断它属于稳定说明、操作手册还是一次性证据。可复现的证据可以提交到本目录；临时截图、数据库、日志和浏览器输出应留在被忽略的本地 artifact 目录。
