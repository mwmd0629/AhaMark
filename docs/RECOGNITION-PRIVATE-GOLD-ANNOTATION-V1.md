# 私有 OCR 与公式 Gold 标注 v2

本流程把仓库外、已授权的私有页面准备成人工 OCR Gold。准备器和标注界面都只在本机工作，不连接数据库或网络，也不运行 OCR。仓库只能保存工具和合成测试；不得提交页面、Gold、来源映射、姓名学号、原始路径或 OCR 正文。

## 1. 准备匿名标注包

输入是 P16/P17 私有诊断清单、单独的私有来源映射和匿名 UUID PNG 目录。输出目录必须尚不存在，避免覆盖人工进度。

```powershell
python scripts/recognition_private_gold_prepare.py `
  <private-cases.json> <private-source-map.json> <private-image-root> <new-private-output-root> `
  --sample-size 60 --max-pages-per-document 3 `
  --scan-target 10 --photo-target 5 --reference-target 25 `
  --draft-predictions <private-tesseract-predictions.json>
```

准备器先校验全部输入图片，再原子发布输出。它会：

- 以匿名 `case_id/document_id` 生成 `annotation-seed.json`；
- 把来源路径只写入单独的 `private-document-map.json`；
- 将选中 PNG 复制到 `images/`，文件名严格为 `<case UUID>.png`；
- 可选地把选中 60 页的既有 OCR 文本提取为独立私有 `ocr-drafts.json`，不复制 Provider、路径或未选页面；草稿按归一化坐标把同一视觉行的词块从左到右重组，只有不同视觉行才换行，避免“多 / 变量 / 函数 / 及 / 其 / 连续 / 性”被错误拆成七段；
- 默认每份来源文档最多抽 3 页，优先覆盖稀缺的扫描件和照片，再平衡参考答案与学生/作业材料；
- 固定输出 `annotation_complete=false`、`accuracy_claim=false`、`writes_product_data=false` 的聚合摘要。

目标数量受“每文档页数上限”和真实可用样本约束，是优先目标而不是伪造配额。若照片只有一页或扫描件集中在少数文档，实际数量可以更少。

## 2. 完全离线标注

直接在浏览器打开 `scripts/recognition_private_gold_annotation_v1.html`：

1. 载入私有输出目录中的 `annotation-seed.json`。
2. 多选载入 `images/` 中全部匿名 PNG。
3. 若包中存在 `ocr-drafts.json`，载入它；工具只会填充尚未编辑的空正文，不会覆盖已经核对的内容。
4. 对照图片修正 OCR 草稿的错字、漏字、数学符号、换行和阅读顺序，再勾选“已逐字核对正文”。任何后续正文编辑都会自动取消该勾选。正文继续使用可搜索的线性 Unicode 表达，不承担二维数学结构真值。
5. 对分式、根号、上下标、极限下标、积分、求和、矩阵或分段函数，切换到“公式区域”，逐个框住完整公式；每个公式填写保留结构的 LaTeX、线性替代文本，并选择“已对照原图核对”或“局部公式无法可靠转写”。编辑已核对公式会自动把它恢复为待核对。
6. 对每页填写题号、题目区域、内容标签、退化标签和完整性决定。有公式框的页面必须包含 `math` 内容标签。
7. 若页面可见姓名、学号、邮箱、电话等身份信息，先在独立图片副本中遮挡并替换该 UUID PNG；工具本身不会猜测或自动擦除身份信息。
8. 只有正文、全部公式、标注状态和隐私复核都完成后，页面才计为完成。
9. 全部页面完成后导出 `gold.json`。

标注自动保存在该浏览器本机的 localStorage，键按随机 `dataset_id` 隔离。图片只以浏览器本地 object URL 显示，不会写入 localStorage 或上传。清除浏览器站点数据会删除未导出的标注进度。

坐标重建只用于减少草稿换行，不是阅读顺序或数学结构真值。公式框及 LaTeX 是新增的结构 Gold；工具不会根据普通 OCR 自动生成或猜测 LaTeX。多栏、复杂矩阵和手写公式仍必须以图片为准核对，局部看不清时只把该公式标为不可可靠转写，不要放弃整页其余可确认内容。

导出时 fail closed：缺图、缺标签、pending 状态、正文未逐字核对、公式未核对、已核对公式缺 LaTeX/线性文本、未完成隐私复核、不可判定页仍含 Gold 内容，或正文/公式出现明显邮箱、手机号、姓名/学号标签及绝对路径，都会阻止导出。导出的 JSON 精确匹配 `recognition-private-gold-v2`，每页新增 `formula_spans`；不会包含 OCR 草稿、正文核对状态、来源、角色或隐私工作字段。旧 v1 标注种子和浏览器进度会在载入时补为空公式集合，不丢失既有正文。

## 标签中文说明

退化标签描述图片为何难识别，可以多选：

- `clean`：清晰，无明显退化。
- `low_resolution`：分辨率低，文字像素不足。
- `blurred`：模糊、失焦或运动模糊。
- `rotation`：页面旋转或倾斜。
- `perspective`：拍照导致梯形、透视变形。
- `low_contrast`：文字与背景对比度低。
- `shadow`：阴影或光照不均。
- `cropped`：内容贴边或疑似被裁切。

内容标签描述页面实际包含什么，也可以多选：

- `chinese`：包含中文。
- `english`：包含英文。
- `math`：包含数学公式或数学符号。
- `question_number`：包含需要识别的题号。
- `negative`：本页没有应识别的题目区域，用于测量误识别；不是“学生答错了”。

## 3. 评分边界

Gold 完成后仍需单独准备预生成 predictions 和仓外 attestation，才能运行：

```powershell
python scripts/recognition_private_benchmark_evaluate.py `
  <gold.json> <predictions.json> <attestation.json> `
  --image-root <deidentified-private-image-root> `
  --output <aggregate-report.json>
```

人工未完成前不得生成 accuracy 报告。现有 benchmark 同时接受 Gold v1/v2，但只继续评估线性文字、题号、题目区域和完整性；报告固定输出 `formula_structure_evaluated=false` 与聚合公式框数量，不能把普通字符指标称为 LaTeX 准确率。公式区域和 LaTeX 专项指标需由后续独立 predictions v2/evaluator 完成。单人自填 attestation 仍只属于 `self_attested_evaluation_only`；公共报告固定不是 production ready，不允许自动确认题目、答案、Rubric、评分或成绩。

本工具能建立可重复的匿名抽样、坐标和文本 Gold 合同，但不能证明标注者确实遮挡了图片中的身份信息，也不能代替双人复核/裁决。真实手写数学、公式结构和复杂阅读顺序应单独记录风险，不能把普通 OCR 文本当作 LaTeX Gold。
