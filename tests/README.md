# 测试目录

后端测试使用 pytest，前端测试与组件或页面相邻存放。`tests/` 中只保存后端测试、稳定 fixture 和共享测试支持代码：

- `fixtures/`：可版本化、无隐私的合成输入和黄金契约；
- `conftest.py`：全局 fixture 与数据库保护；
- `structured_rubric_support.py`：Structured Rubric 测试辅助；
- `test_*.py`：按业务或基础设施能力命名的测试模块。

数据库测试必须使用隔离临时路径，并保留 `ahamark.db unchanged` 守卫。不要把真实学生资料、服务器导出、临时 SQLite、pytest cache、截图或容量输出提交到本目录；一次性产物应进入被 `.gitignore` 覆盖的位置，稳定且脱敏的验收证据进入 `docs/`。
