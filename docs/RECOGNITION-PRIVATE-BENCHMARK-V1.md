# 私有离线 OCR Benchmark v1

`recognition_private_benchmark_evaluate.py` 只在本机读取严格 JSON 和已授权私有图片，评测预先生成的预测；它不连接数据库或网络，不下载或加载模型，也不写产品数据。

```powershell
python scripts/recognition_private_benchmark_evaluate.py `
  <gold.json> <predictions.json> <attestation.json> `
  --image-root <private-image-directory> --output <aggregate-report.json>
```

Gold、predictions 与 attestation 必须分开保存。Gold 使用随机 UUID `case_id/document_id`，按 `document_id` 隔离 train/dev/test；公开评测只纳入经仓外 attestation 声明为真实、去标识、已授权且本地取得的 held-out test。四个必需模态是 `text_pdf/scan/photo/mixed`。退化标签包括 clean、低清、模糊、旋转、透视、低对比、阴影和裁切。

图片目录只能包含被评测 case 对应的 `<case UUID>.png`，拒绝路径穿越、符号链接、缺失/额外文件、损坏图片和尺寸不符。单图压缩文件不得超过 20 MiB，声明及解码尺寸不得超过 4000 万像素，以免恶意或误配置本地文件耗尽资源。仓库不得提交私有图片、gold、原文、路径、来源 hash 或人员信息。

Predictions 必须且只能覆盖目标 case，并以 canonical JSON SHA-256 在解盲前封存。报告提供：Levenshtein CER/character accuracy、English WER、数学 token precision/recall/F1/edit rate、题号页面 exact 与 anchor precision/recall、IoU region precision/recall 与困难负页 FP、latency mean/p50/p95、peak memory、manual-required ratio 和 integrity confusion matrix。数学指标只处理带 `math` content tag 的页面；token 包括 LaTeX command、单字符拉丁变量、Greek、数字、Unicode 数学/上下标和显式运算/结构符，连续普通英文单词不作为数学 token。CER/WER/math token edit rate 通常以 gold 单位数为分母；当某聚合层 gold 单位数为零但存在 observed 插入时，改用 observed 单位数，故纯插入错误率为 1、character accuracy 为 0；两侧都为空时错误率为 0。math、question、region、integrity 的 precision/recall 等比率在对应支持数为零时输出 JSON `null`，并同时输出 `support` 计数，绝不以 1 冒充满分。指标只按 overall、四模态和退化标签聚合；overall 或任一层少于两个独立文档时都只报告计数与 `suppressed=true`。

公共报告不包含文本、ID、路径、框、hash 或自由输入 detector/provider 名称和版本。当前 attestation schema 不绑定可信 detector 身份，因此报告固定输出 `detector_identity.trusted_identity_verified=false`；同时固定为 `status=self_attested_evaluation_only`、`eligible_for_pilot=false`、`production_ready=false`、`writes_product_data=false`、`human_confirmation_required=true`，并包含 `TRUSTED_ATTESTATION_REQUIRED`。自填 attestation 和 adapter 上报的性能不能证明真实来源、授权、盲评或计时真实性；接入可信 registry 或签名 runner 前不得作为 pilot/生产放行证据。
