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
   需要给画布和右侧标注区腾出横向空间时，点击左栏顶部“收起左栏”；整栏会缩成窄边，右侧标注栏同步增宽。再次点击“展开左栏”即可恢复，折叠偏好只保存在本机浏览器。
   页面较多时可点击左侧“页面列表”标题，或标题右侧蓝色的“收起列表 / 展开列表”提示，收起或重新展开整段列表；标题右侧会保留总页数，收起不会改变当前页或标注进度。
4. 对照图片修正 OCR 草稿的错字、漏字、数学符号、换行和阅读顺序，再勾选“已逐字核对正文”。任何后续正文编辑都会自动取消该勾选。正文继续使用可搜索的线性 Unicode 表达，不承担二维数学结构真值。
5. 对分式、根号、上下标、极限下标、积分、求和、矩阵或分段函数，切换到“公式区域”，逐个框住完整公式。公式卡片按“输入或套用常用结构 → 查看结构草稿 → 对照原图作出判断”三步排列。优先在“按平时写法输入公式”中使用下方有限语法；停止输入 450 ms 后会自动生成 LaTeX、线性替代文本和完全离线的结构预览，也可按 `Ctrl+Enter` 或点击“立即生成草稿”。生成结果固定保持“待核对”，只有人工点击“与原图一致”才会改为已核对；编辑普通输入、LaTeX 或线性替代文本都会自动撤销已核对状态。旧草稿在新输入成功转换前不能被误确认。LaTeX、线性替代文本和原始状态默认折叠在“高级编辑”中。
6. 对每页填写题号、题目区域、内容标签、退化标签和完整性决定。有公式框的页面必须包含 `math` 内容标签。
7. 若页面可见姓名、学号、邮箱、电话等身份信息，先在独立图片副本中遮挡并替换该 UUID PNG；工具本身不会猜测或自动擦除身份信息。
8. 只有正文、全部公式、标注状态和隐私复核都完成后，页面才计为完成。
9. 全部页面完成后导出 `gold.json`。

标注自动保存在该浏览器本机的 localStorage，键按随机 `dataset_id` 隔离。图片只以浏览器本地 object URL 显示，不会写入 localStorage 或上传。清除浏览器站点数据会删除未导出的标注进度。

坐标重建只用于减少草稿换行，不是阅读顺序或数学结构真值。公式框及 LaTeX 是新增的结构 Gold；工具不会根据普通 OCR 自动生成或猜测 LaTeX。多栏、复杂矩阵和手写公式仍必须以图片为准核对，局部看不清时只把该公式标为不可可靠转写，不要放弃整页其余可确认内容。

### 普通数学输入的有限语法

转换器是无依赖的本地确定性解析器，不读取图片、不连接网络、不调用模型。它只生成草稿，不证明数学等价，也不会自动标记 `reviewed`。普通输入只存在于当前页面内存，不写入 localStorage 或最终 Gold；生成后的 `latex`、`linear_text` 和人工核对状态仍按 v2 schema 保存。

支持的明确结构：

- `[a]/[b]` 或 `(a)/(b)`：分式。分子、分母必须显式括起；`a/b` 会拒绝。
- `sqrt(...)` 或 `√(...)`：根号，括号明确覆盖范围。
- `x^2`、`x^(...)`、`x_1`、`x_(...)`：上标和下标。
- `lim x->0`、`lim (x,y)->(0,0)`：极限下标；也接受 Unicode `→`。
- `int_a^b`、`sum_(i=1)^n`：带上下限的积分和求和。
- `matrix([a,b];[c,d])`：矩阵；每行列数必须相同。
- `cases([f(x),x>=0];[g(x),x<0])`：分段函数；每行必须恰好包含表达式和条件。
- 常见 Unicode `−`、`α`、`β`、`∞`，以及 `<`、`>`、`≤`、`≥`。

工具栏的“分式、根号、上标、下标”会优先包裹当前选中文字；没有选区时插入占位模板并选中待填写部分。“极限、积分、求和、矩阵、分段函数”会插入上述严格模板并把焦点放到第一个占位内容。嵌套结构必须逐层使用括号，例如 `[sqrt([a]/[b])+x^2]/(y_(i+1))`。连续裸除法、缺括号、空上下标、不等长矩阵、缺条件的分段函数、不支持字符或无法唯一确定的结构都会停止转换并显示位置，不会猜测。自动转换失败时只提示继续输入；点击“立即生成草稿”或按 `Ctrl+Enter` 才显示精确停止原因。用户可以选择“需要修改”展开高级编辑，或选择“需要辅助转写”继续保持 pending；只有原图局部确实无法可靠辨认时才使用 `unreadable`，不把整页改为 `unjudgeable`。

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
