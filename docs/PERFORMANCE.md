# 第八部分性能报告（2026-07-22）

## 结论

- 第六部分验收：**PASS（开发机有界容量）**。单 API、单 Worker 的目标合成规模已经完成，
  详见下方第六部分更新及 `PERFORMANCE-CAPACITY.md`。
- 生产容量结论：**PARTIAL / NOT ESTABLISHED**。没有证明生产 SLA、多实例扩展、故障条件
  或生产数据分布；最大规模 Analytics 学生趋势/详情约 7.7–7.9 秒。

以下是第六部分之前的历史单客户端顺序冒烟：真实 PostgreSQL、单 API 容器下，2 个班级、
每班 50 名合成学生、2 份作业、每份 20 题的五类同步接口全部成功，P95 均小于 100 ms。
该历史结果不单独代表并发或生产容量。

| 场景 | 请求 | 成功率 | P50 | P95 | 最大 |
|---|---:|---:|---:|---:|---:|
| 登录 | 5 | 100% | 45.48 ms | 82.33 ms | 82.33 ms |
| 班级列表 | 30 | 100% | 13.24 ms | 40.51 ms | 51.56 ms |
| 50 人学生列表 | 30 | 100% | 48.73 ms | 69.82 ms | 160.69 ms |
| 作业列表 | 30 | 100% | 65.07 ms | 88.18 ms | 94.67 ms |
| 20 题作业详情 | 30 | 100% | 39.02 ms | 63.10 ms | 66.84 ms |

原始证据见 `docs/performance-results.json`。运行命令：

```powershell
docker compose exec -T api python -m app.cli.seed_performance_demo
python scripts/performance_smoke.py
```

没有基于这组有限数据增加索引或重构查询。作业列表相对较慢但仍在建议开发门槛内；需要并发和 SQL 采样后再决定优化。

## 第六部分容量更新（2026-07-23）

新的隔离并发矩阵与异步阶梯见 `PERFORMANCE-CAPACITY.md`。同步证据链保留原始基线
`sync-capacity-baseline.json`（failed）、中间轮次 `sync-capacity-results.json`
（failed）和最终轮次 `sync-capacity-optimized.json`（passed）。50/100/200 人学生列表
N+1 及 20/50/100 题详情 N+1 已修复；最终轮次详情并发 20 P95 为
184.78/199.72/348.81 ms。
Fake OCR 编排 150/200/250 页和真实 RapidOCR 100/150/250 页均完成；50 名不同学生报告
及 50/100/200 人 Analytics 已通过。第六部分按开发机有界容量关闭，不得声明生产容量。
