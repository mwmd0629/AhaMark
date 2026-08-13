"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { ApiError, classesApi, type ClassRecord } from "@/lib/api";
import {
  collectionItems,
  teachingResourcesApi,
  type TeachingResource,
} from "@/lib/student-api";
import { formatDateTime } from "@/lib/student-format";

const labels: Record<string, string> = {
  ppt: "课程 PPT",
  handout: "课程讲义",
  reference: "参考资料",
  link: "网络资源",
  web: "网络资源",
};

export default function TeachingResourcesPage() {
  const toast = useToast();
  const [resources, setResources] = useState<TeachingResource[]>([]);
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [resourceType, setResourceType] = useState("ppt");
  const [classId, setClassId] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [resourceData, classData] = await Promise.all([
        teachingResourcesApi.list(),
        classesApi.list("page_size=100&status=active"),
      ]);
      setResources(
        collectionItems(resourceData).filter(
          (resource) => resource.status !== "archived",
        ),
      );
      setClasses(classData.items);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "教学资源加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !classId) {
      setFormError("请填写资源标题并选择发布班级。");
      return;
    }
    if (resourceType === "web" && !url.trim()) {
      setFormError("网络资源必须填写链接。");
      return;
    }
    if (resourceType !== "web" && !file) {
      setFormError("请先选择需要发布的文件。");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      const uploaded = file
        ? await teachingResourcesApi.uploadFile(file)
        : null;
      try {
        await teachingResourcesApi.create({
          title: title.trim(),
          description: description.trim() || undefined,
          resource_type: resourceType,
          class_id: classId,
          url: resourceType === "web" ? url.trim() : undefined,
          stored_file_id: uploaded?.id,
        });
      } catch (reason) {
        if (uploaded) {
          await teachingResourcesApi
            .deleteUpload(uploaded.id)
            .catch(() => undefined);
        }
        throw reason;
      }
      setTitle("");
      setDescription("");
      setUrl("");
      setFile(null);
      toast("教学资源草稿已创建");
      await load();
    } catch (reason) {
      setFormError(
        reason instanceof ApiError ? reason.message : "教学资源创建失败。",
      );
    } finally {
      setSaving(false);
    }
  };

  const changePublication = async (resource: TeachingResource) => {
    setFormError("");
    try {
      if (resource.status === "published") {
        await teachingResourcesApi.unpublish(resource.id);
        toast("资源已停止发布");
      } else {
        await teachingResourcesApi.publish(resource.id);
        toast("资源已发布给学生");
      }
      await load();
    } catch (reason) {
      setFormError(
        reason instanceof ApiError ? reason.message : "资源状态更新失败。",
      );
    }
  };

  const remove = async (resource: TeachingResource) => {
    if (
      !window.confirm(
        `确认归档资源“${resource.title}”吗？归档后学生将无法查看。`,
      )
    )
      return;
    setFormError("");
    try {
      await teachingResourcesApi.remove(resource.id);
      toast("资源已归档");
      await load();
    } catch (reason) {
      setFormError(
        reason instanceof ApiError ? reason.message : "资源归档失败。",
      );
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="教学资源"
        description="为指定班级创建并发布课程 PPT、讲义、参考资料和可信网络资源。学生只能看到已发布内容。"
      />

      <Card className="p-5">
        <h2 className="font-bold">新增教学资源</h2>
        <form onSubmit={submit} className="mt-5 grid gap-4 lg:grid-cols-2">
          <Input
            label="资源标题"
            required
            value={title}
            maxLength={160}
            onChange={(event) => setTitle(event.target.value)}
          />
          <Select
            label="资源类型"
            value={resourceType}
            onChange={(event) => {
              setResourceType(event.target.value);
              setFile(null);
              setUrl("");
            }}
          >
            <option value="ppt">课程 PPT</option>
            <option value="handout">课程讲义</option>
            <option value="reference">参考资料</option>
            <option value="web">网络资源</option>
          </Select>
          <Select
            label="发布班级"
            required
            value={classId}
            onChange={(event) => setClassId(event.target.value)}
          >
            <option value="">请选择班级</option>
            {classes.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          {resourceType === "web" ? (
            <Input
              label="资源链接"
              required
              type="url"
              placeholder="https://example.com/resource"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          ) : (
            <label className="grid gap-1.5 text-sm font-medium">
              资源文件 <span className="sr-only">必填</span>
              <input
                type="file"
                required
                accept=".pdf,.pptx,.docx,.png,.jpg,.jpeg"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="block h-10 w-full rounded-xl border border-[var(--border)] bg-white p-1 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-[var(--brand-50)] file:px-3 file:py-1.5 file:font-semibold file:text-[var(--brand-700)]"
              />
            </label>
          )}
          <div className="lg:col-span-2">
            <Textarea
              label="资源说明"
              value={description}
              maxLength={2000}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          {formError && (
            <p role="alert" className="text-sm text-red-700 lg:col-span-2">
              {formError}
            </p>
          )}
          <div className="lg:col-span-2">
            <Button type="submit" loading={saving}>
              创建草稿
            </Button>
          </div>
        </form>
      </Card>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((item) => (
            <Skeleton key={item} className="h-52" />
          ))}
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : resources.length ? (
        <section
          aria-label="教学资源列表"
          className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
        >
          {resources.map((resource) => (
            <Card key={resource.id} className="flex flex-col p-5">
              <div className="flex items-start justify-between gap-3">
                <span className="rounded-full bg-[var(--brand-50)] px-2.5 py-1 text-xs font-semibold text-[var(--brand-700)]">
                  {labels[resource.resource_type] || resource.resource_type}
                </span>
                <span
                  className={`rounded-full px-2.5 py-1 text-xs font-semibold ${resource.status === "published" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-700"}`}
                >
                  {resource.status === "published" ? "已发布" : "草稿"}
                </span>
              </div>
              <h2 className="mt-4 text-lg font-bold">{resource.title}</h2>
              <p className="mt-2 flex-1 text-sm leading-6 text-[var(--text-secondary)]">
                {resource.description || resource.original_name || "暂无说明"}
              </p>
              <p className="mt-4 text-xs text-[var(--text-secondary)]">
                {resource.class_name || "指定班级"} ·{" "}
                {formatDateTime(resource.created_at)}
              </p>
              <div className="mt-4 flex flex-wrap gap-2 border-t border-[var(--border)] pt-4">
                <Button
                  type="button"
                  variant={
                    resource.status === "published" ? "outline" : "secondary"
                  }
                  onClick={() => void changePublication(resource)}
                >
                  {resource.status === "published" ? "停止发布" : "发布给学生"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => void remove(resource)}
                >
                  删除
                </Button>
              </div>
            </Card>
          ))}
        </section>
      ) : (
        <EmptyState
          title="暂无教学资源"
          description="使用上方表单创建第一份资源草稿，确认无误后再发布给学生。"
          icon="resources"
        />
      )}
    </div>
  );
}
