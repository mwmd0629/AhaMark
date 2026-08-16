# 私有 OCR Gold 标注 v1

本流程把仓库外、已授权的私有页面准备成人工 OCR Gold。准备器和标注界面都只在本机工作，不连接数据库或网络，也不运行 OCR。仓库只能保存工具和合成测试；不得提交页面、Gold、来源映射、姓名学号、原始路径或 OCR 正文。

## 1. 准备匿名标注包

输入是 P16/P17 私有诊断清单、单独的私有来源映射和匿名 UUID PNG 目录。输出目录必须尚不存在，避免覆盖人工进度。

```powershell
python scripts/recognition_private_gold_prepare.py `
  <private-cases.json> <private-source-map.json> <private-image-root> <new-private-output-root> `
  --sample-size 60 --max-pages-per-document 3 `
  --scan-target 10 --photo-target 5 --reference-target 25
```

准备器先校验全部输入图片，再原子发布输出。它会：

- 以匿名 `case_id/document_id` 生成 `annotation-seed.json`；
- 把来源路径只写入单独的 `private-document-map.json`；
- 将选中 PNG 复制到 `images/`，文件名严格为 `<case UUID>.png`；
- 默认每份来源文档最多抽 3 页，优先覆盖稀缺的扫描件和照片，再平衡参考答案与学生/作业材料；
- 固定输出 `annotation_complete=false`、`accuracy_claim=false`、`writes_product_data=false` 的聚合摘要。

目标数量受“每文档页数上限”和真实可用样本约束，是优先目标而不是伪造配额。若照片只有一页或扫描件集中在少数文档，实际数量可以更少。

## 2. 完全离线标注

直接在浏览器打开 `scripts/recognition_private_gold_annotation_v1.html`：

1. 载入私有输出目录中的 `annotation-seed.json`。
2. 多选载入 `images/` 中全部匿名 PNG。
3. 对每页填写正文、题号、题目区域、内容标签、退化标签和完整性决定。
4. 若页面可见姓名、学号、邮箱、电话等身份信息，先在独立图片副本中遮挡并替换该 UUID PNG；工具本身不会猜测或自动擦除身份信息。
5. 只有标注状态和隐私复核都完成后，页面才计为完成。
6. 全部页面完成后导出 `gold.json`。

标注自动保存在该浏览器本机的 localStorage，键按随机 `dataset_id` 隔离。图片只以浏览器本地 object URL 显示，不会写入 localStorage 或上传。清除浏览器站点数据会删除未导出的标注进度。

导出时 fail closed：缺图、缺标签、pending 状态、未完成隐私复核、不可判定页仍含 Gold 内容，或正文出现明显邮箱、手机号、姓名/学号标签及绝对路径，都会阻止导出。导出的 JSON 精确匹配 `recognition-private-gold-v1`，不会包含来源、角色或隐私工作字段。

## 3. 评分边界

Gold 完成后仍需单独准备预生成 predictions 和仓外 attestation，才能运行：

```powershell
python scripts/recognition_private_benchmark_evaluate.py `
  <gold.json> <predictions.json> <attestation.json> `
  --image-root <deidentified-private-image-root> `
  --output <aggregate-report.json>
```

人工未完成前不得生成 accuracy 报告。单人自填 attestation 仍只属于 `self_attested_evaluation_only`；公共报告固定不是 production ready，不允许自动确认题目、答案、Rubric、评分或成绩。

本工具能建立可重复的匿名抽样、坐标和文本 Gold 合同，但不能证明标注者确实遮挡了图片中的身份信息，也不能代替双人复核/裁决。真实手写数学、公式结构和复杂阅读顺序应单独记录风险，不能把普通 OCR 文本当作 LaTeX Gold。
