# Formula region detection v1

`formula-region-detection-v1` 是独立、脱敏、可版本化的公式框评测契约。它不含图片位置、页面正文或真实来源映射，也不连接 AhaMark 数据库。

## 公开数据契约

```json
{
  "schema_version": "formula-region-detection-v1",
  "dataset_id": "00000000-0000-4000-8000-000000000001",
  "annotator_decision_version": "decision-v1",
  "cases": [
    {
      "case_id": "00000000-0000-4000-8000-000000000002",
      "document_id": "00000000-0000-4000-8000-000000000003",
      "split": "test",
      "modality": "scan",
      "page_width": 1600,
      "page_height": 2200,
      "contains_formula": true,
      "regions": [
        {
          "region_id": "00000000-0000-4000-8000-000000000004",
          "bbox": { "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.08 },
          "kind": "display",
          "print_style": "printed",
          "quality_flags": ["none"],
          "annotation_status": "confirmed"
        }
      ],
      "quality_flags": ["none"],
      "negative_tags": [],
      "annotation_status": "annotated",
      "annotator_decision_version": "decision-v1"
    }
  ]
}
```

允许值：

- `modality`: `text_pdf / scan / photo / synthetic`
- `split`: `train / dev / test`
- `kind`: `inline / display / multiline / matrix / unknown`
- `print_style`: `printed / handwritten / mixed / unknown`
- 页面 `annotation_status`: `annotated / no_formula / unjudgeable`
- 区域 `annotation_status`: `confirmed / ignored`
- `quality_flags`: `none / blurred / faint / perspective / ruled_paper / overwritten / occluded / cropped / low_resolution`
- `negative_tags`: `body_text / table / geometry / separator / ruled_paper / underline / header_footer / numeric_label / chinese_punctuation / table_border / overwritten_area / faint_or_blurred`

所有层级使用精确字段集合。ID 必须是随机 UUID；坐标是相对源图左上角的 0–1 值，框必须为正面积且完全位于页面内；IoU ≥ 0.9 的人工重复框被拒绝。同一 `document_id` 只能属于一个 split，防止同文档版式泄漏。

公开 JSON 严禁包含：原文件名、路径、姓名/学号、班级/作业/数据库 ID、页面正文、原 PDF 哈希、图片字段或可反查来源的信息。验证器递归检查字段名和疑似绝对路径字符串。必要的真实来源映射和图片只能保留在仓库外私有目录。

## 离线标注工具

打开 `scripts/formula_region_annotation_v1.html`，选择仓库外 PNG/JPEG。图片只存入浏览器私有 IndexedDB `ahamark-formula-region-private-images-v1`；公开导出不含图片、路径或文件名。人工状态与随机 ID 自动保存在独立 localStorage 键 `ahamark-formula-region-annotation-v1`。

载入图片只创建随机页面壳，状态为内部 `pending`，不形成“有公式”“无公式”或“无法标注”等人工决定，且 pending 页面不能导出。工具支持 20%–200% 缩放、多框、新增/选择/重画/删除、区域类型与印刷样式、显式“本页无公式”和“无法可靠标注”。导出前检查 pending、坐标、重复框、页面状态、document split，并只导出公开契约字段。浏览器缩放只影响显示尺寸；SVG viewBox 和导出 bbox 均基于源图像素。

标注集应包含无公式正文页及表格、几何图、分隔线、横格纸、下划线、页眉页脚、普通数字编号、中文括号/标点、表格边框、涂改、极淡/模糊等困难负样本；正样本应覆盖行内/独立/多行/矩阵、分式、根式、积分/求和/极限、偏导、上下标、向量、手写/印刷和拍照透视。

## 验证与评测

```powershell
python scripts/formula_region_detection_synthetic_baseline.py <dataset.json> <image-dir> <predictions.json>
python scripts/formula_region_detection_evaluate.py <dataset.json> <predictions.json> <report.json>
```

`synthetic-baseline` 只接受全部为 `synthetic` 的数据，并要求图片目录精确为每个 `<case_id>.png`，不接受符号链接或额外文件；暗色连通组件仅用于验证 evaluator 的确定性非产品基线。

评测默认 IoU ≥ 0.5，按 IoU 降序、region ID、proposal ID 做确定性一对一贪心匹配。一个真值对应多个预测时只有一个 TP，其余为 FP；一个预测覆盖多个真值时只有一个 TP，其余为漏检，同时记录 merge error。与 ignored 区域 IoU ≥ 0.5 的预测排除；unjudgeable 页面排除；空负样本页上的每个框都是 FP。

报告包含 Precision、Recall、F1、TP/FP/漏检、每页 FP、重复框、fragmentation、merge error、formula coverage、proposal 数、逐页/均值/分位耗时、可用时的峰值内存，以及公开权重的 manual workload proxy。结果按 modality、print style、region kind 和 negative pages 分层，并固定：

```json
{
  "production_ready": false,
  "human_confirmation_required": true,
  "writes_product_data": false
}
```

当前没有持久化真实标注集，也没有任何真实 detector 分层指标；不能据 synthetic 结果选择或接入产品 detector。

## 纯离线 Pilot readiness gate

`formula_region_detection_readiness.py` 是独立于产品运行时的元数据门禁。它只读取严格
JSON，不读取图片，不下载模型，不导入应用数据库，也不创建 `FormulaRegion` 或其他产品记录：

```powershell
python scripts/formula_region_detection_readiness.py `
  <dataset.json> <predictions.json> <attestation.json> --output <readiness-report.json>
```

输入 dataset 和 predictions 继续遵循上一节的 v1 契约。额外 attestation 使用
`formula-region-readiness-v1`，只保存随机文档 ID、去标识状态、仓库外来源/许可证明的随机 ID、
授权布尔值、detector 名称与版本、代码/权重许可证标识、本地获取授权，以及盲审流程证明。
禁止写入 URL、路径、文件名、图片、checksum、人员身份或可反查学校/作业/数据库的信息。
本地获取授权只证明已有本地副本可用于评测；validator 本身永远不执行下载。

readiness policy v1 只统计 `test` split 中 `real_deidentified` 且可判定的真实样本：

- `text_pdf / scan / photo` 每种至少 30 个独立 `document_id`、100 个页面；
- 每种至少 50 个有公式页面和 20 个无公式困难负例页面；
- 每个公开 `negative_tags` 至少覆盖 10 个独立真实文档；
- 同一文档不得跨 train/dev/test；synthetic 数量不计入任何真实样本门槛；
- predictions 必须且只能覆盖上述 pilot 页面；有 prediction 行但没有有效 proposal 不算质量通过；
- 仅在上述真实 held-out test 子集运行既有确定性 evaluator；overall 及 `text_pdf / scan / photo`
  每层 Precision、Recall、formula coverage 均不低于 0.90，fragmentation/ground truth 与 merge
  error/ground truth 均不高于 0.05；困难负页单独要求每页 false positive 不高于 0.10；任何一层、
  任何一项越界都会产生独立 blocker，其他模态或正样本页不能稀释该失败；
- 每个真实文档必须声明去标识、评测用途授权和本地获取授权；
- detector 代码与权重许可证必须分别确认，且模型只能来自已授权本地副本或本地构建；
- 必须声明至少两名独立标注者、完成裁决且不在公开清单保存人员身份；预测封存时间必须早于
  标签解盲时间，并用 canonical JSON 的 SHA-256 绑定已封存 predictions，防止封存后本地修改。
  SHA-256 仅存在仓库外 attestation，公开报告不回显。该声明仍只是自证明流程证据，不是对人员
  身份、授权、held-out 来源或实际行为的可信第三方证明。

readiness report 不回显自由输入的 detector 名称/版本，只用固定
`detector_identity.verified_against_private_attestation=true` 表示二者已与私有 attestation 一致；此外只包含
非 synthetic 的 overall/by-modality/negative-pages 聚合指标、聚合计数和稳定 blocker codes，不包含 case/document
明细、公式、框坐标或来源材料。结果始终固定：

```json
{
  "enabled": false,
  "production_ready": false,
  "human_confirmation_required": true,
  "writes_product_data": false
}
```

在尚未实现受控 trust registry 或签名验证前，报告固定为
`status=self_attested_evaluation_only`、`eligible_for_pilot=false`，并始终包含
`TRUSTED_ATTESTATION_REQUIRED`。所有其余数据、授权、盲审、覆盖和质量合同通过时，仅得到
`self_attested_evaluation_complete=true`。这不修改配置，不启用 detector，不表示生产就绪，也不改变
“教师显式确认”的产品边界。
