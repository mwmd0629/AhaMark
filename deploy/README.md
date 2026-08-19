# 部署资源

`deploy/` 保存进入镜像或目标环境的静态部署资源：

- `nginx/`：开发、预生产和 node2 的反向代理配置；
- `node2/`：node2 运行环境准备脚本；
- `rapidocr/`：固定 RapidOCR artifact 的许可说明与校验清单。

根目录的 `docker-compose*.yml` 是各环境的编排入口。不要在本目录保存 `runtime.env`、证书私钥、数据库备份、模型权重或服务器导出文件。部署和回滚流程见 `docs/OPERATIONS.md`、`docs/BACKUP-RESTORE.md`；当前线上事实仍以根目录 `README.md` 为准。
