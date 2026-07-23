# 第六部分：容量、并发与异步任务验证

更新日期：2026-07-23
基线提交：`8ba3a413c4d7864506294ce728fa4f4dffeefce2`

## 结论

状态：**PASS（开发机有界容量）**。6A、6B 的预定关闭条件均满足，第六部分可以关闭；
项目等级仍为 C，本结果不构成生产容量、OCR 准确率、手写或公式能力声明。

## 环境

- Intel Core Ultra 9 275HX，24 逻辑核；Docker 24 CPU、15.34 GiB。
- PostgreSQL 16.14、Redis 7.4.9、MinIO 2025-04-22。
- 单 API、单 Worker；Celery concurrency=24、prefetch=1、soft/hard timeout=1740/1800 秒。
- 全部为 `APP_ENV=test` 隔离合成数据，无真实学生内容。

## 6A：同步容量与详情优化

同步容量保留三个真实运行轮次，失败轮次用于定位问题，不应冒充最终通过证据：

| 阶段 | 文件 | 结果 | 定位 |
|---|---|---|---|
| 原始基线 | `sync-capacity-baseline.json` | failed | 优化前问题证据 |
| 中间轮次 | `sync-capacity-results.json` | failed | 部分优化后详情仍未达标 |
| 最终轮次 | `sync-capacity-optimized.json` | passed | 6A 正式关闭证据 |

三轮均使用同一数据集、PostgreSQL、单 API、并发模型和 Docker 资源。最终轮次共 1600
请求，1600/1600 成功，无超时、无 5xx。第六部分 PASS 基于最终轮次及各专项最终证据，
不表示所有保留的历史运行都 passed。

学生列表 N+1 从约 103 条 SQL 降至不超过 6 条。作业详情原实现对每题分别读取 Region、
KnowledgePoint、Rubric 和 RubricItem，并在发布检查中重复读取 Paper/Question/Rubric。
修复后对上述数据批量读取并复用发布检查输入；100 题详情回归测试查询数不超过 12，
响应字段保持不变。

| 场景（并发 20） | 中间失败轮次 P95 | 最终通过轮次 P95 |
|---|---:|---:|
| 20 题详情 | 785.47 ms | 184.78 ms |
| 50 题详情 | 1450.62 ms | 199.72 ms |
| 100 题详情 | 2913.18 ms | 348.81 ms |
| 50 人列表 | 983.05 ms | 149.45 ms |
| 100 人列表 | 1795.58 ms | 303.01 ms |
| 200 人第一页 | 1744.78 ms | 266.88 ms |
| 200 人第二页 | 1375.84 ms | 257.52 ms |

优化前详情响应体为 6785/15948/31197 bytes；没有改变 API 响应结构或前端。测试未启用
`pg_stat_statements`，因此数据库/Python/序列化分段时间、连接池等待和精确 API CPU
没有形成可靠机器证据；根因结论来自 SQL 查询计数、代码路径和同配置前后复测。

## 6B：OCR

### Fake OCR 编排

证据：`ocr-orchestration-capacity.json`。150/200/250 页均经 API→Redis→Celery→
PostgreSQL/MinIO 完整运行，耗时 8.488/11.383/16.163 秒，全部页面 completed。
每级 PageResult/Block/Candidate 分别为 150/150/150、200/200/200、250/250/250；
页码从 1 连续到目标页数，衍生键分别为 450/600/750 个且唯一。

首次 150 页暴露 Candidate 临时编号跨页冲突，并使异常 Job 停在 running；失败证据保留，
Job 已明确标记 failed。修复为全 Job 唯一编号，并在 Worker 未预期异常时可靠落 failed。
250 页完成后再次派发同一 Job，业务行数与衍生键均未增长。该结果只证明编排，不证明
RapidOCR 性能或准确率。

### 真实 RapidOCR

证据：`ocr-capacity-results.json`。每级独立进程、运行时生成 1240×1754 清晰印刷体 PNG；
保留原 25/50 页结果。

| 页数 | 成功 | 总时长 | 单页 P50/P95 | 峰值 RSS |
|---:|---:|---:|---:|---:|
| 25 | 25 | 79.558 s | 3059.68/3909.06 ms | 812 MiB |
| 50 | 50 | 141.240 s | 2806.58/3234.92 ms | 880 MiB |
| 100 | 100 | 307.984 s | 2973.96/3624.61 ms | 878 MiB |
| 150 | 150 | 453.606 s | 2967.66/3624.37 ms | 879 MiB |
| 250 | 250 | 756.412 s | 2951.88/3559.21 ms | 878 MiB |

所有页面有文本，失败/空白均为 0，未触发 soft/hard timeout。100–250 页 RSS 无持续增长，
模型每个独立进程只初始化一次。开发机明确通过上限为 250 页。

## 50 名不同学生报告

证据：`async-capacity-results.json`。固定 GradeRelease 含 50 个不同学生和 50 个 complete
Snapshot；52/52 Job completed（50 PDF、1 XLSX、1 ZIP），失败和重试均为 0。

- 创建 API P50/P95：40.72/79.52 ms。
- PDF 执行 P50/P95：585.35/1457.13 ms；全部完成约 12.256 秒。
- XLSX：13,525 bytes，517.57 ms；实际 50 行，50 个唯一学号，全部文本单元格。
- ZIP：1,324,881 bytes，11.607 秒；实际 50 个唯一安全文件名，50 个条目均有 PDF 头。
- 50 个 PDF Job 的 student_id 全部不同；ReportJob key 无重复，成功 Job 均有独立
  StoredFile 引用。

## Analytics

证据：`analytics-capacity-results.json`。同一固定 GradeRelease、schema 1.0 在事务内锁定
Release，锁后复查并复用最早 complete AnalyticsSnapshot；不依赖进程内锁。新 Release
仍创建独立 Snapshot，旧 Snapshot 不覆盖。

- 50/100/200 名 complete 学生、20/50/100 题均生成成功，source 数分别为 50/100/200。
- 每级顺序重复 5 次均只有 1 个 snapshot ID。
- 200 人级 20 路并发创建全部 201，仍只有 1 个 snapshot ID。
- 分数段、题目、KnowledgePoint、错误类型下钻，班级趋势、学生趋势、学生详情均 200；
  TeachingInsight 均 201。
- 最大规模下钻约 3.5–4.1 秒，学生趋势/详情约 7.7–7.9 秒，存在明显超线性退化；
  功能与一致性通过，但这是真实的开发机读取容量边界。

## 一致性与门禁

- 最终 RecognitionJob、ReportJob queued/running 均为 0；Celery active/reserved 均为空。
- 三个容量 Release 各只有 1 个 AnalyticsSnapshot；ReportJob key 无重复；本轮报告没有
  缺文件或重复 StoredFile 引用。
- Fake OCR 页面、Block、Candidate 和衍生键对账一致；重复派发没有新增业务行。
- 全局历史 orphan 扫描仍受 recognition 派生对象口径影响；没有删除任何对象或历史孤儿，
  本轮已知前缀内未发现新增未知孤儿。
- Ruff format/check：PASS；mypy strict：PASS（40 个源文件）。
- Pytest：87 passed、2 skipped、1 条第三方 Starlette TestClient 弃用警告。测试 fixture
  增加 `close_all_sessions()`，避免 PostgreSQL DDL 等待未关闭测试 Session。
- 前端与 API 响应结构未改，未重复运行前端门禁；无新增迁移。
