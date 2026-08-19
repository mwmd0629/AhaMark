# 脚本导航

`scripts/` 保存可重复执行的开发、验收和运维辅助程序。脚本名称按用途分组：

- `verify_*`：只读或受控的契约、运行时和恢复验证；
- `*_browser_e2e.*`、`*_browser_acceptance.*`：浏览器业务验收；
- `*_capacity_test.py`、`*_concurrency_test.py`：容量与并发测试；
- `*_evaluate.py`、`*_evaluation_*`：离线评测和阈值配置；
- `stage3_*`、`stage4_*`：历史阶段验收入口；
- `assignment_generation_*`：作业生成专项；
- `formula_*`、`recognition_*`：识别与公式专项；
- `acquire_*`、`start_*`、`local_*_provider*`：本地固定模型或服务工具。

运行脚本前先阅读脚本顶部保护条件和对应 `docs/` 说明。浏览器验收必须使用合成账号与隔离数据；私有识别脚本当前保持暂停。脚本生成的临时数据库、日志、截图和构建目录不得放入 Git，长期证据应经过脱敏后写入 `docs/`。

常用质量门禁从仓库根目录执行：

```powershell
python -m ruff check apps/api/app workers tests
python -m mypy
python -m pytest
npm.cmd run test
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
```
