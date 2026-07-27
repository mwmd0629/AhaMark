# 测试数据库事件记录（2026-07-26）

## 事件与发现

- 事件发生在 2026-07-26 的第三部分完整后端测试验收期间；只读审计时间为
  2026-07-26 13:49:36 +08:00（中国标准时间）。
- 受影响文件为
  `D:\OpenAIData\.codex\worktrees\3fe4\AhaMark\ahamark.db`。
- 报告时已知修改时间为 2026-07-26 13:19:03 +08:00，大小为
  2,158,592 bytes。审计所得 SHA-256 为
  `2F7CC45C46BFBDDF5A2348959F50DD00385AC36D2DC9498DD33D60855E1D8F22`。
- 事件由完整 `pytest` 执行后检查工作树数据库发现。该文件不是符号链接、
  Junction 或 ReparsePoint，不受 Git 跟踪，并由 `*.db` 忽略规则覆盖。

## 原因与影响范围

直接原因是测试的 autouse schema fixture 对模块级全局 engine 执行了
`drop_all`、`create_all`，并在测试结束时再次执行 `drop_all`。该 engine 实际指向
工作树中的 `./ahamark.db`。

根因链为：`pytest` 启动 → `tests/conftest.py` 在模块顶层导入数据库模块 →
`app.db.session` 在导入期调用带 `lru_cache` 的 Settings → 使用生产保持不变的默认值
`sqlite:///./ahamark.db` 创建模块级 engine → 测试环境变量设置得太晚，无法重绑定
该 engine → fixture 对既有数据库执行破坏性 schema 重建。`APP_ENV=test` 只改变
provider 行为，不会改变已创建 engine 的 URL。

0018、0019、0020 迁移测试原本各自显式创建 `tmp_path` SQLite engine；未发现测试
代码删除 `ahamark.db`、对它直接运行 Alembic，或执行文件级恢复操作。本次影响确认
为上述 schema fixture 指向错误目标。事件前数据库是否含有业务数据无法由当前内容
确认。

## 只读数据库检查

只使用 SQLite URI 的 `mode=ro&immutable=1` 读取：当前数据库
`schema_version=239528`、`user_version=0`，`sqlite_master` 中没有表，也没有
`alembic_version`。高 schema version 与反复 create/drop 相符，但不能单独证明事件前
内容。相邻的 `ahamark.db-wal`、`ahamark.db-shm`、`ahamark.db-journal`、
`ahamark.db.backup` 和 `ahamark.db.bak` 均不存在。immutable 读取不会合并 WAL；本次
检查没有执行 checkpoint、写入型 PRAGMA 或任何写连接。

## 恢复候选只读评估

检查范围严格限制为当前工作树和 Git 主工作区：

- `D:\OpenAIData\Workspaces\AhaMark\ahamark.db`：1,286,144 bytes，修改时间
  2026-07-25 00:56:52 +08:00，SHA-256
  `F90FD1EEA25EE7F62178B41D5D1A194C5E7154C4EE86A5B78329E8BCF43A5C75`。
  immutable 只读检查只见空的 `alembic_version` 表，没有业务表；不能确认是事件前
  完整副本，直接覆盖风险高。
- `D:\OpenAIData\Workspaces\AhaMark\ahamark-test.db`：1,265,664 bytes，修改时间
  2026-07-23 16:59:52 +08:00，SHA-256
  `A737EFA98FD10C4A3AF9D957DBBA2DE494BF68F6BE5CE0C04035B04F91A38F79`。
  immutable 只读检查没有表；不能确认是恢复源，直接覆盖风险高。
- `.recovery-v7` 范围内只发现 JSON 报告，没有 SQLite 数据库备份。

在本任务获准检查的目录范围内未找到可确认的恢复来源。用户主目录、旧项目、
Windows File History/Previous Versions、Docker volume、预生产数据库及其他工作树均未获
授权读取或导出。若要继续恢复评估，应由用户另行明确批准具体来源、目标和只读检查
方式；任何恢复前还应先保全当前文件及可能的 sidecar，并在副本上验证，不能直接覆盖。

## 隔离修复

- `tests/conftest.py` 现在在导入任何应用、数据库、模型、Worker 或 TestClient 前创建
  唯一的系统临时目录及会话所有权 marker，并强制设置绝对 SQLite `DATABASE_URL`。
- 启动时清除尚未安全实例化的 Settings 缓存，并验证模块级 engine 的规范化路径就是
  当前会话目标；若应用数据库模块已提前导入则 fail-fast。
- 所有 destructive schema setup/teardown 前均重新验证 marker、session、worker、
  规范化父目录、精确文件名及禁止根目录。
- 相对、空、内存、无法解析、预先存在、marker 缺失/伪造以及解析后落入工作树或 Git
  主目录的目标均被拒绝。
- 每个并行 worker 和迁移测试使用独立的带 marker 临时数据库。清理只允许删除当前
  marker 明确拥有的数据库及其三个精确 sidecar 名称，不使用 glob。
- session 结束时再次比较受影响数据库的 SHA-256、大小、mtime 和 sidecar 状态；任何
  变化都会强制测试会话失败。

## 新增安全测试与重新验收

数据库隔离专项覆盖默认环境、绝对临时路径、全局 engine/Settings、工作树和预存文件
拒绝、marker 所有权、路径解析、Windows 大小写/斜杠、并行 worker、Worker import、
精确清理及伪造目标等边界。首轮最终结果为 18 passed、1 skipped；跳过项是当前 Windows
环境不能创建测试符号链接。测试前后受影响文件保持同一 SHA-256、2,158,592 bytes、
同一 UTC mtime ticks `639206399433661871`，且 WAL/SHM/journal 均继续不存在。

分轮重新验收结果：

- 第一轮隔离专项最终为 18 passed、1 skipped；临时数据库父目录为系统临时目录下的
  `ahamark-pytest-main-*`，外层 `--basetemp` 为 `ahamark-pytest-guard-*`。
- 第二轮使用新的 `ahamark-pytest-main-*` 会话目录以及独立迁移数据库，第三部分、
  0018/0019/0020、Recognition 和 Assignment 共 43 passed、2 skipped。0020 的
  upgrade/downgrade/upgrade、约束检查和 PostgreSQL upgrade/downgrade 离线 SQL 均通过。
- 第三轮完整后端测试使用新的 `ahamark-pytest-main-fa4zdurr` 会话目录，结果为
  228 passed、3 skipped、37 warnings。每轮开始输出均确认 marker 已验证、危险目标
  守卫已启用及全局 engine 指向临时文件。
- 前端第三部分聚焦 Vitest 为 2 passed；完整 Vitest 为 19 files、50 tests passed。
  Ruff format、Ruff check、mypy（88 个源文件）、Prettier、ESLint、TypeScript typecheck、
  `git diff --check`、唯一 Alembic head `0020_assignment_question_extraction` 和 Next
  production build 均通过。Next build 输出了尝试修补缺失 SWC lockfile 条目失败的
  非致命警告，但编译、类型检查、17 个静态页生成和构建进程均成功。

三轮以及最终守卫复跑前后，受影响文件始终保持 SHA-256
`2F7CC45C46BFBDDF5A2348959F50DD00385AC36D2DC9498DD33D60855E1D8F22`、
2,158,592 bytes、UTC mtime ticks `639206399433661871`，且 WAL/SHM/journal 均不存在。
最终守卫复跑仍为 18 passed、1 skipped。

## 恢复状态

当前 `ahamark.db` 未被恢复；本任务没有从其他目录复制数据库，没有合并 WAL，没有运行
`.recover`，也没有删除、移动、重命名、覆盖或迁移该文件。测试通过只表示隔离门禁有效，
不表示数据恢复。任何恢复操作仍需用户对具体来源、目标和方式另行明确授权。
