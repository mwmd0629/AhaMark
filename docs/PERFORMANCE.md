# 第八部分性能报告（2026-07-22）

## 结论

状态：**PARTIAL**。真实 PostgreSQL、单 API 容器、单客户端顺序请求下，2 个班级、每班 50 名合成学生、2 份作业、每份 20 题的五类同步接口全部成功，P95 均小于 100 ms。该结果只是开发延迟冒烟，不代表并发容量，也不覆盖 150–250 页上传、OCR 吞吐、Review Queue、finalize、报告、AnalyticsSnapshot、数据库查询数、CPU/内存或队列等待。

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
