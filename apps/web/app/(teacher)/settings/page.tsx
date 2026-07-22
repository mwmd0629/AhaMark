"use client";
import { HealthStatus } from "@/components/health-status";
import {
  Button,
  Card,
  DemoBadge,
  Input,
  PageHeader,
  SectionHeader,
  Select,
  useToast,
} from "@/components/ui";
import { demoTeacher } from "@/lib/demo-data";
export default function SettingsPage() {
  const toast = useToast();
  return (
    <div className="space-y-6">
      <PageHeader
        title="设置"
        description="当前表单仅保存于页面状态，不代表真实账户或后端配置。"
        eyebrow={<DemoBadge />}
      />
      <div className="grid gap-6 xl:grid-cols-[220px_1fr]">
        <nav
          aria-label="设置分区"
          className="h-fit rounded-xl border border-[var(--border)] bg-white p-2"
        >
          {[
            "个人信息",
            "通知设置",
            "AI 批改偏好",
            "数据与隐私",
            "系统连接状态",
          ].map((item, i) => (
            <a
              key={item}
              href={`#setting-${i}`}
              className="block rounded-lg px-3 py-2.5 text-sm hover:bg-slate-50"
            >
              {item}
            </a>
          ))}
        </nav>
        <div className="space-y-6">
          <Card id="setting-0" className="p-5">
            <SectionHeader
              title="个人信息"
              description="演示教师，未来由认证上下文提供"
            />
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <Input label="姓名" defaultValue={demoTeacher.name} />
              <Input label="职务" defaultValue="数学教师" />
            </div>
          </Card>
          <Card id="setting-1" className="p-5">
            <SectionHeader title="通知设置" />
            <label className="mt-5 flex items-center gap-3 text-sm">
              <input type="checkbox" defaultChecked />
              作业提交与待复核提醒（本地演示）
            </label>
          </Card>
          <Card id="setting-2" className="p-5">
            <SectionHeader title="AI 批改偏好" />
            <div className="mt-5">
              <Select label="低置信度处理">
                <option>始终要求教师复核</option>
                <option>仅标记提醒</option>
              </Select>
            </div>
          </Card>
          <Card id="setting-3" className="p-5">
            <SectionHeader title="数据与隐私" />
            <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
              真实数据留存与导出策略尚未接入。此页面不会向后端提交个人信息。
            </p>
          </Card>
          <Card id="setting-4" className="p-5">
            <SectionHeader
              title="系统连接状态"
              description="真实 API 健康检查"
            />
            <div className="mt-4">
              <HealthStatus />
            </div>
          </Card>
          <Button onClick={() => toast("演示设置已暂存；刷新页面后不会保留")}>
            保存本地演示设置
          </Button>
        </div>
      </div>
    </div>
  );
}
