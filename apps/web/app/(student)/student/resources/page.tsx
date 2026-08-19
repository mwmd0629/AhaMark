"use client";

import { useEffect, useState } from "react";
import { Card, EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { studentPortalApi, type ClassResource } from "@/lib/api";

const typeNames: Record<ClassResource["resource_type"], string> = {
  exercise: "习题",
  handout: "讲义",
  reference: "参考资料",
  other: "其他",
};

export default function StudentResourcesPage() {
  const [items, setItems] = useState<ClassResource[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    studentPortalApi
      .resources()
      .then(setItems)
      .catch(() => setError("学习资料加载失败，请稍后重试。"));
  }, []);
  if (!items) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-6">
      <PageHeader
        title="学习资料"
        description="这里只显示教师明确发布给你所在班级的资料。"
      />
      {error && (
        <Card className="border-red-300 p-4 text-red-700">{error}</Card>
      )}
      {!items.length ? (
        <EmptyState
          icon="assignments"
          title="暂无学习资料"
          description="教师发布班级讲义或练习后会显示在这里。"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {items.map((item) => (
            <Card className="p-5" key={item.id}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-bold">{item.title}</h2>
                  <p className="mt-1 text-sm text-slate-600">
                    {item.file_name} · {item.page_count} 页
                  </p>
                </div>
                <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs text-blue-700">
                  {typeNames[item.resource_type]}
                </span>
              </div>
              <a
                className="mt-5 inline-block font-medium text-blue-700 hover:underline"
                href={studentPortalApi.resourceDownloadUrl(item.id)}
              >
                下载资料
              </a>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
