# 第五部分 5B：文件安全 fixture 验证

验证日期：2026-07-23；数据均为运行时生成的纯合成结构。

## 结论

**PASS（所列 fixture 范围内）**。没有保存真实恶意软件。统一检查在对象写入前完成；
学生作业先全批验证，再写对象和数据库。通用上传、试卷上传、学生批量上传和报告
Worker 均在存储或数据库失败时回滚事务并尽力删除本次精确对象键，不做前缀清理。

## Fixture 结果

- 通用/文件名：空内容、超长名、控制字符、路径型名称、basename、MIME/扩展/魔数
  不一致；对象键为 owner 前缀加 UUID。
- PDF：有效单页/多页通过；假、损坏、截断、加密、零页、全空白、超页数及类型不一致
  拒绝。
- PNG/JPEG：有效图片通过；假图、截断、超像素、格式不一致拒绝；像素限制在完整解码
  前检查，EXIF 转置错误映射为安全拒绝。
- DOCX/XLSX：有效 Office ZIP 通过；假 ZIP、路径穿越、缺核心文件、宏、外部关系、
  损坏 XML、超过 2000 条目和极端单条目压缩比拒绝。
- 表格：XLSX 公式拒绝；CSV/字段以 `= + - @` 或控制前缀开头时拒绝；报告输出仍转义
  公式。
- 批次：首/中/末非法均因全批预检而零写入；重复校验值返回冲突；第二次对象写失败
  和数据库提交失败的故障注入均验证对象/StoredFile/Submission/SubmissionPage 无新增。

## StoredFile 与签名 URL

未认证为 401；Teacher B 的 metadata、签名和删除均为 404。业务文件不能通过通用
delete 删除。隔离栈把 `SIGNED_URL_EXPIRY_SECONDS` 设为 2，仅影响 test；production
默认仍为 900 秒。真实 MinIO 验证旧 URL 2 秒后 403，重新鉴权签发的新 URL 可用，旧
URL 不恢复。机器证据不保存 URL 查询参数。

## 孤儿说明

本轮创建的 `uploads/{owner}/{uuid}.png` 同时存在 StoredFile 和对象，安全运行新增孤儿
为 0。全 Bucket 只读扫描仍会把未建 StoredFile 的 recognition 派生图列为
`object_missing_database`，并包含此前 retry fixture 的缺对象记录；这些是既有模型/
扫描口径问题，不属于本轮新增，未自动删除。

## 证据与限制

- 自动化：`tests/test_file_security.py`、`tests/test_file_security_matrix.py`
- HTTP/MinIO：`authorization-http-verification.json`
- 机器汇总：`file-security-verification.json`

结论只覆盖上述结构 fixture；不是杀毒引擎、沙箱执行或生产 WAF 证明。
