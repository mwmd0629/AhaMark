# Assignment Generation Evaluation v1

第六部分使用版本化 JSONL 与离线 CLI 建立可重复评测，不新增生产数据库表，也不修改
0018–0022。黄金集入口为
`tests/fixtures/assignment_generation_evaluation_v1.jsonl`，Schema 为同目录
`assignment_generation_evaluation_v1.schema.json`。v1 有 16 条纯合成 case，覆盖电子/扫描/
图片/多文件、空白/重复/旋转/低质量/损坏/MIME 冲突、页面与题目边界、跨页与多区域、
子题/双栏/题号与分值冲突、线性代数答案与 Rubric、Prompt Injection、stale/readiness 和
发布并发。第三方教材、真实学生数据与真实官方答案均未进入 Git。

## 指标、分母与 null

`scripts/assignment_generation_evaluate.py` 计算文件角色、答案来源、页数/顺序、空白与重复
precision/recall、低质量/缺页/variant recall；题号/数量/边界 IoU、边界
precision/recall、跨页/多区域/父子关系、类型、规范文本、CER、分值与缺失分值 abstention；
答案结构与来源保持；Rubric schema、分值、dependency、替代路径、partial credit、路由和
evidence；blocking、误阻断、高置信错误、教师修改以及安全硬计数；另观察 latency、token、
图片、成本与重试。

accuracy 的分母是适用 gold 实体数；precision 分母是预测阳性，recall 分母是 gold 阳性；
IoU 只计 gold 与 prediction 都有合法归一化框的题目；CER 先移除空白并 case-fold，分母是
gold 字符数；缺失 score 只进入 abstention 分母。分母为零、字段未采集或 fake/unavailable
不得产生的真实 telemetry 均为 JSON `null`。null 不通过阈值，且不以 0 伪造。case 不会
因为单个指标不适用而从其他指标分母消失。

## 冻结门禁与 run 隔离

阈值文件为 `assignment-generation-evaluation-thresholds-v1.json`，在运行前冻结为
structural、safety、fake flow、real provider 和 performance observation 五层。真实
Provider 最低样本量是 30；安全八项计数必须精确为 0。每次 run 写入新
`.preproduction-assignment-generation/<run_id>/`，目录已存在即失败，不覆盖或拼接旧
run。结果保存数据集/阈值/prompt/schema/provider/model、工作树摘要、时间、case、指标、
失败与证据哈希，不保存 secret 或原始敏感材料。

Fake 只能证明确定性流程；unavailable 只能证明正确降级，两者不能证明质量。只有真实
Provider、至少 30 条合法可审计 gold、完整成本/token/延迟以及所有冻结门禁均通过，才允许
REAL-PROVIDER QUALITY PASS。否则为 PENDING 或 FAIL。AI/第三方答案不是官方答案。
