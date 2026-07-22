import {
  Button,
  DemoBadge,
  EmptyState,
  PageHeader,
  StatCard,
} from "@/components/ui";
export default function PracticePage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="错题与练习"
        description="从教师确认后的错题生成针对性练习；目前尚无真实错题数据。"
        eyebrow={<DemoBadge />}
        actions={<Button disabled>生成练习</Button>}
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="待整理错题" value="—" note="等待真实批改数据" />
        <StatCard label="已生成练习" value="—" note="功能尚未接入" />
        <StatCard label="覆盖知识点" value="—" note="功能尚未接入" />
      </div>
      <EmptyState
        icon="practice"
        title="还没有可用错题"
        description="完成一次教师复核后，确认的错题将出现在这里。练习生成不会在本阶段执行。"
      />
    </div>
  );
}
