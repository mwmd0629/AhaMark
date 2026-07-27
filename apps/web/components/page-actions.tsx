"use client";
import { Button, Dialog, Input, Select, useToast } from "./ui";
export function CreateAssignmentAction() {
  const toast = useToast();
  return (
    <Dialog
      title="创建作业"
      description="当前为界面演示，不会写入后端。"
      dismissible={false}
      trigger={<Button>创建作业</Button>}
    >
      <div className="grid gap-4">
        <Input label="作业名称" required placeholder="例如：二次函数单元测验" />
        <Select label="班级">
          <option>初二（3）班（演示）</option>
        </Select>
        <Button onClick={() => toast("演示表单已验证；尚未接入保存接口")}>
          保存演示
        </Button>
      </div>
    </Dialog>
  );
}
export function UploadAction() {
  const toast = useToast();
  return (
    <Dialog
      title="上传学生作业"
      description="OCR 与 AI 评分尚未接入。"
      dismissible={false}
      trigger={<Button>上传学生作业</Button>}
    >
      <div className="rounded-xl border border-dashed border-[var(--border)] p-8 text-center text-sm text-[var(--text-secondary)]">
        后续在此复用真实上传组件。
        <div>
          <Button
            className="mt-4"
            variant="outline"
            onClick={() => toast("演示上传入口可用；未发送文件")}
          >
            选择文件（演示）
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
