# 应用目录

`apps/` 只保存可部署的应用代码，不保存测试输出、模型文件或运行时数据。

- `api/`：FastAPI 应用、SQLAlchemy 模型、Alembic 迁移和受控 CLI。
- `web/`：Next.js/React 前端；页面、组件及其相邻测试放在同一功能目录。

跨应用异步任务位于仓库根目录的 `workers/`，端到端和容量脚本位于 `scripts/`。当前产品状态与安全边界以根目录 `README.md` 的“接手必读”为准。
