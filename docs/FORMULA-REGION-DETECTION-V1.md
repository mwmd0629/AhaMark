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
