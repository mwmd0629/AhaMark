# 公式区域 detector 候选比较（2026-08-14）

本报告只比较公式区域框，不比较 LaTeX。当前未下载或执行任何候选权重；因此除合成连通组件 smoke baseline 外，指标均为“未测”，不能据宣传指标选择产品 detector。

## 本地基线与依赖事实

- 当前项目相关运行时只有 Pillow；未发现 Paddle/PaddleOCR/PaddleX、PyTorch/torchvision、OpenCV、Ultralytics、DocLayout-YOLO、Pix2Text/CnSTD 或 ONNX Runtime。
- `synthetic-connected-component-v1` 只接受 `synthetic` 页面，通过宽/高/长宽比过滤暗色连通组件。1 页、1 个 printed/display 公式的 smoke 结果为 Precision/Recall/F1/coverage `1/1/1/1`，0 FP、0 fragmentation、0 merge；其他模态、手写、行内、多行、矩阵与负页均未测。它只证明 manifest→proposal→report 链路，禁止进入产品。

## 排名与适配判断

| 排名                        | 候选                            | 公式类别与输出                                                                                   | 扫描/照片/手写证据                                                           | 运行与依赖                                                                                                        | 许可证风险                                                                            | AhaMark 适配与主要预期误差                                                                             |
| --------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| 1（优先离线评测）           | Pix2Text MFD 1.5                | 专用 MFD，API 区分 `embedding`/`isolated` 并返回四点框；只取检测输出，不调用 LaTeX recognition   | 面向中英文混排图像；没有 scan/photo/handwritten detector 分层指标            | 本地 Python，基于 CnSTD；公开模型带 ONNX 标签，但完整依赖含 CnSTD/CnOCR/OpenCV，模型大小与稳定 CPU/GPU 延迟未公开 | Pix2Text 代码与公开 MFD 模型卡均为 MIT；必须固定免费 `mfd-1.5` 文件，避免混入付费型号 | inline/display 映射最清楚，四点框转最小外接矩形后适配中等；重点测多行/矩阵合框、照片透视与自动下载边界 |
| 2（优先离线评测）           | PaddleOCR `PP-DocLayout-M/S/L`  | 官方 layout 类别含 `formula` 和 `formula number`，输出区域框；没有证据证明行内公式会稳定独立成框 | 训练说明包含中英文 papers/books/exams；照片透视和手写公式没有分层指标        | S/M/L 权重约 4.834/22.578/123.76 MB；官方 CPU 高性能推理约 6.29/24.44/251.08 ms（不含预后处理）                   | PaddleOCR 代码 Apache-2.0；权重是否有单独条款仍需逐项确认                             | 矩形可直接映射离线契约，适配成本低；重点压测表格边框、题号、普通数字、行内漏检、照片退化               |
| 3（许可证审查后评测）       | PDF-Extract-Kit MFD `YOLOv8_ft` | 官方明确区分 inline/block formulas，矩形框                                                       | 官方称用中英文公式文档微调；没有公开的本任务 scan/photo/handwritten 分层指标 | Python 3.10 + Ultralytics；权重大小、CPU/显存和延迟需下载前审计                                                   | 仓库与官方 1.0 权重仓库均标 AGPL-3.0，产品接入风险高                                  | 类别最贴合，映射成本低；YOLO/NMS 需重点测多行碎片、相邻公式合并和小行内公式                            |
| 4（仅作完整解析栈对照）     | MinerU pipeline                 | 自动识别公式并输出中间 layout 信息；底层组合 layout/MFD/MFR                                      | 支持扫描 PDF、图片与 CPU/GPU，但未提供 AhaMark 四模态区域指标                | 完整解析栈、模型与服务依赖明显高于独立 detector                                                                   | MinerU Open Source License 基于 Apache-2.0 但有附加条件；还需核对所选底层模型许可     | 需从中间 JSON 抽公式框，适配中高；全栈会引入无关 OCR/表格能力与依赖冲突                                |
| 5（研究基线，不建议先集成） | ScanSSD                         | 视觉方式检测 inline/display，矩形框；论文 IoU≥0.5 F1=0.796                                       | born-digital 与 scanned 学术文档；无中文试卷、照片透视或手写证据             | 600 DPI、1200×1200 滑窗、10% stride，CPU/node2 成本可能很高；旧 SSD 代码维护风险                                  | 论文称代码公开，但仓库/权重许可仍需单独确认                                           | 论文已明确大空隙导致 fragmentation、相邻行导致 merge、宽图像误报公式，正好是本评测器的关键指标         |

## 官方依据

- PaddleOCR PP-StructureV3 模型类别、大小和官方延迟：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md>
- Pix2Text MFD 1.5、inline/isolated 输出和 MIT 代码许可：<https://github.com/breezedeus/Pix2Text>
- Pix2Text 公开 MFD 模型卡（MIT）：<https://huggingface.co/breezedeus/pix2text-mfd>
- PDF-Extract-Kit 的 inline/block MFD、依赖与 AGPL：<https://github.com/opendatalab/PDF-Extract-Kit>
- PDF-Extract-Kit 1.0 权重仓库（AGPL-3.0）：<https://huggingface.co/opendatalab/PDF-Extract-Kit-1.0>
- UniMERNet 的 MFD 教程入口（只涉及检测，不能把识别模型冒充 detector）：<https://github.com/opendatalab/UniMERNet/blob/main/MFD/README.md>
- MinerU 能力与附加条件许可证说明：<https://github.com/opendatalab/MinerU/blob/master/docs/en/index.md>
- ScanSSD 论文：<https://arxiv.org/abs/2003.08005>
- 可借鉴但尚未导入的公开 bbox benchmark：<https://github.com/opendatalab/OmniDocBench>

## 人类审查门结论

当前 `text_pdf/scan/photo` 已标注页数均为 0，所有真实候选的 recall、每页 FP、多行 fragmentation、merge、运行时与峰值内存均未测。产品接入门不成立；不创建迁移、不修改正式 API、不新增模型服务。下一步应先用 v1 标注页构建 document-level 隔离的脱敏 dev/test，然后由用户在“Pix2Text MFD 1.5”“Paddle PP-DocLayout-S/M”与“PDF-Extract-Kit MFD（接受 AGPL 审查）”之间选择首个待下载评测候选。
