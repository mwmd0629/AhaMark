# 版本化数据

`data/` 只保存可以公开复现、无隐私且需要随代码版本管理的小型数据集。当前 `linear_algebra_evaluation_v1.json` 是线性代数合成评测输入。

真实试卷、答卷、姓名、学号、OCR 私有 Gold、服务器导出、模型权重和大体积生成物不得放入本目录或 Git。测试专用 fixture 应放入 `tests/fixtures/`，评测结果与验收结论应脱敏后放入 `docs/`。
