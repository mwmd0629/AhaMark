# Local OCR runtime notice

The node2 OCR image pins `rapidocr==3.9.2` (Apache-2.0 package metadata) and
`onnxruntime==1.28.0` (MIT). The three ONNX files are copied from the installed
RapidOCR wheel during the image build and must match `manifest.json` exactly.

This approval covers only local printed-text OCR with CPUExecutionProvider.
Runtime downloads, handwritten-answer reliability claims, formula recognition,
automatic grading, and unattended grade release remain prohibited.
