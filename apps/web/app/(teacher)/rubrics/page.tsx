import {
  Badge,
  Button,
  Card,
  DemoBadge,
  Input,
  PageHeader,
  Select,
} from "@/components/ui";
import { demoRubrics } from "@/lib/demo-data";
export default function RubricsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="评分模板"
        description="建立一致的评分规则，供后续 AI 初批和教师复核复用。"
        eyebrow={<DemoBadge />}
        actions={<Button>创建评分模板</Button>}
      />
      <Card className="grid gap-3 p-4 md:grid-cols-[1fr_200px]">
        <Input aria-label="搜索模板" placeholder="搜索评分模板" />
        <Select aria-label="学科筛选">
          <option>全部学科</option>
          <option>数学</option>
        </Select>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {demoRubrics.map((item) => (
          <Card key={item.name} className="p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="font-bold">{item.name}</h2>
                <p className="mt-2 text-sm text-[var(--text-secondary)]">
                  {item.subject} · 更新于 {item.updatedAt}
                </p>
              </div>
              <Badge status={item.status === "草稿" ? "draft" : "completed"} />
            </div>
            <p className="mt-5 border-t border-[var(--border)] pt-4 text-sm text-[var(--text-secondary)]">
              演示模板，不参与真实评分。
            </p>
          </Card>
        ))}
      </div>
    </div>
  );
}
