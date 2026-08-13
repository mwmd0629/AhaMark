"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type TeachingResource,
} from "@/lib/student-api";
import { formatDateTime, safeExternalUrl } from "@/lib/student-format";

const resourceLabels: Record<string, string> = {
  ppt: "课程 PPT",
  handout: "课程讲义",
  reference: "参考资料",
  link: "网络资源",
  web: "网络资源",
};

function ResourceOpenButton({ resource }: { resource: TeachingResource }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const external = safeExternalUrl(resource.url);

  if (external) {
    return (
      <a
        href={external}
        target="_blank"
        rel="noopener noreferrer"
        className="font-semibold text-[var(--brand-700)]"
      >
        打开资源
      </a>
    );
  }

  if (!resource.stored_file_id)
    return <span className="text-xs text-red-700">暂无可用链接</span>;

  const open = async () => {
    const target = window.open("about:blank", "_blank");
    if (!target) {
      setError("浏览器阻止了新窗口，请允许本站打开弹窗后重试。");
      return;
    }
    target.opener = null;
    setLoading(true);
    setError("");
    try {
      const result = await studentApi.resourceSignedUrl(resource.id);
      const url = safeExternalUrl(result.url);
      if (!url) throw new Error("unsafe_resource_url");
      target.location.replace(url);
    } catch (reason) {
      target.close();
      setError(reason instanceof ApiError ? reason.message : "资源打开失败。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <span className="text-right">
      <button
        type="button"
        disabled={loading}
        onClick={() => void open()}
        className="font-semibold text-[var(--brand-700)] disabled:opacity-50"
      >
        {loading ? "正在获取…" : "打开资源"}
      </button>
      {error && (
        <span role="alert" className="mt-1 block text-xs text-red-700">
          {error}
        </span>
      )}
    </span>
  );
}

export default function StudentResourcesPage() {
  const [resources, setResources] = useState<TeachingResource[]>([]);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setResources(collectionItems(await studentApi.resources()));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学习资源加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);
  const visible = useMemo(
    () =>
      resources.filter(
        (item) => filter === "all" || item.resource_type === filter,
      ),
    [filter, resources],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="学习资源"
        description="查看教师向你所在班级发布的课程 PPT、讲义、参考资料和网络资源。"
        actions={
          <Select
            aria-label="按资源类型筛选"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">全部类型</option>
            <option value="ppt">课程 PPT</option>
            <option value="handout">课程讲义</option>
            <option value="reference">参考资料</option>
            <option value="web">网络资源</option>
          </Select>
        }
      />
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3, 4, 5, 6].map((item) => (
            <Skeleton key={item} className="h-48" />
          ))}
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : visible.length ? (
        <section
          aria-label="学习资源列表"
          className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
        >
          {visible.map((resource) => {
            return (
              <Card key={resource.id} className="flex min-h-48 flex-col p-5">
                <div className="flex items-start justify-between gap-3">
                  <span className="rounded-full bg-[var(--brand-50)] px-2.5 py-1 text-xs font-semibold text-[var(--brand-700)]">
                    {resourceLabels[resource.resource_type] ||
                      resource.resource_type}
                  </span>
                  <span className="text-xs text-[var(--text-secondary)]">
                    {resource.subject}
                  </span>
                </div>
                <h2 className="mt-4 text-lg font-bold">{resource.title}</h2>
                <p className="mt-2 line-clamp-3 flex-1 text-sm leading-6 text-[var(--text-secondary)]">
                  {resource.description ||
                    resource.original_name ||
                    "教师发布的课程资源"}
                </p>
                <div className="mt-4 flex items-end justify-between gap-3">
                  <span className="text-xs text-[var(--text-secondary)]">
                    {formatDateTime(resource.published_at)}
                  </span>
                  <ResourceOpenButton resource={resource} />
                </div>
              </Card>
            );
          })}
        </section>
      ) : (
        <EmptyState
          title="暂无学习资源"
          description={
            filter === "all"
              ? "教师发布的资源会显示在这里。"
              : "当前筛选类型下没有已发布资源。"
          }
          icon="resources"
        />
      )}
    </div>
  );
}
