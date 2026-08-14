# 本机公式识别服务

本服务把仓库外的 PaddleOCR `PP-FormulaNet_plus-M` 模型接入 AhaMark 已有 HTTP
Formula Provider 契约。它只处理 AhaMark 已裁切的单个 PNG 公式区域，不接收整份 PDF，
不自动确认识别结果，也不生成评分或成绩。

## 启动

在单独的 PowerShell 会话中注入临时令牌。不要把令牌写入仓库、README、命令历史或截图：

```powershell
$env:AHAMARK_FORMULA_PROVIDER_TOKEN = Read-Host "本机公式服务令牌"
& .\scripts\start_local_formula_provider.ps1 `
  -PythonExe "<独立运行时>\Scripts\python.exe" `
  -ModelDir "<PP-FormulaNet_plus-M 模型目录>" `
  -Port 8765
```

脚本固定监听 `127.0.0.1`，检查端口、模型的三个必需文件、Python 依赖和至少 32 字符
令牌。进程以前台方式运行；在该 PowerShell 会话按 `Ctrl+C` 即可停止。

## AhaMark 开发配置

只在本机开发环境注入以下配置，不要把真实值写入 `.env.example`：

```text
FORMULA_RECOGNITION_PROVIDER=http
FORMULA_RECOGNITION_BASE_URL=http://127.0.0.1:8765
FORMULA_RECOGNITION_API_KEY=<与服务相同的临时令牌>
FORMULA_RECOGNITION_ALLOWED_HOSTS=["127.0.0.1"]
```

`GET /health` 只检查进程存活；带同一 Bearer token 的 `GET /ready` 会实际加载模型，
首次调用可能需要较长时间。AhaMark 自身 `/ready` 会用最多一秒的软依赖探测显示
`formula_ocr` 是否可用，但公式服务失败不会让数据库、存储等主系统被误判为不可用。
公开或脱敏合成 PNG 可用 `scripts/local_formula_provider_smoke.py` 验证。AhaMark 默认仍为
`unavailable`；没有明确配置时不会调用该模型。

## 脱敏分层评测

先由人工在仓库外制作单公式 PNG 裁图，并给每张图分配不含姓名、学号、文件名或来源信息
的随机 case ID。清单采用 `formula-ocr-provider-eval-v1`，每条只允许 `id`、`modality`、
`expected_latex`、`region_kind`；图片目录必须且只能包含同名 `<case-id>.png`。`modality`
使用 `text_pdf`、`scan`、`photo` 或 `synthetic`，不要把真实 PDF 或整页图交给评测脚本。

```powershell
$env:AHAMARK_FORMULA_PROVIDER_TOKEN = Read-Host "本机公式服务令牌"
python .\scripts\formula_ocr_provider_evaluate.py `
  "<仓库外脱敏清单.json>" `
  "<仓库外脱敏 PNG 目录>" `
  --output "<仓库外报告.json>"
```

报告不记录图片路径，按模态给出保守 LaTeX 精确率、token 相似度和人工复核率；
`production_ready` 固定为 `false`，`human_confirmation_required` 固定为 `true`。
只有经授权的脱敏标注集才可用于质量判断，单个公开合成样例只能验证链路。

## 结果边界

服务只返回一条最高候选。当前 Paddle 模型没有输出可校准置信度，因此响应固定保留
`confidence=null`、`UNCALIBRATED_CONFIDENCE` 和 `TEACHER_REVIEW_REQUIRED`。
候选必须由教师核对和明确确认；公开样例通过不代表手写公式或真实数学作业准确率。
