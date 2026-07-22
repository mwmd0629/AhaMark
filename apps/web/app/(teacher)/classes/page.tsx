"use client";
/* eslint-disable react-hooks/exhaustive-deps */
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import {
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import { ApiError, classesApi, type ClassRecord } from "@/lib/api";

export default function ClassesPage() {
  const [items, setItems] = useState<ClassRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("active");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({ page: String(page), status, search });
      const data = await classesApi.list(q.toString());
      setItems(data.items);
      setPages(Math.max(data.pages, 1));
    } catch (e) {
      setError(e instanceof Error ? e.message : "班级列表加载失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => window.clearTimeout(timer);
  }, [search, status, page]);
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    const form = new FormData(event.currentTarget);
    try {
      await classesApi.create({
        name: String(form.get("name")),
        grade: String(form.get("grade") || ""),
        subject: String(form.get("subject") || ""),
        academic_year: String(form.get("academic_year") || ""),
        semester: String(form.get("semester") || ""),
      });
      event.currentTarget.reset();
      toast("班级已创建");
      await load();
    } catch (e) {
      toast(e instanceof ApiError ? e.body.message : "创建失败", "error");
    } finally {
      setSaving(false);
    }
  };
  const changeStatus = async (item: ClassRecord) => {
    if (
      !window.confirm(
        item.status === "active"
          ? "归档后不可用于创建新作业，但不会删除学生。确认归档？"
          : "确认恢复这个班级？",
      )
    )
      return;
    try {
      if (item.status === "active") await classesApi.archive(item.id);
      else await classesApi.restore(item.id);
      toast(item.status === "active" ? "班级已归档" : "班级已恢复");
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "操作失败", "error");
    }
  };
  return (
    <div className="space-y-6">
      <PageHeader
        title="班级与学生"
        description="管理真实班级、学生、分组与名单导入。"
        actions={
          <Dialog
            title="创建班级"
            description="同一教师下班级名称不可重复。"
            trigger={
              <Button>
                <Icon name="plus" className="h-4 w-4" />
                创建班级
              </Button>
            }
          >
            <form className="grid gap-4" onSubmit={create}>
              <Input label="班级名称" name="name" required maxLength={120} />
              <div className="grid grid-cols-2 gap-3">
                <Input label="年级" name="grade" />
                <Input label="学科" name="subject" />
                <Input
                  label="学年"
                  name="academic_year"
                  placeholder="2025-2026"
                />
                <Input label="学期" name="semester" />
              </div>
              <Button type="submit" loading={saving}>
                保存班级
              </Button>
            </form>
          </Dialog>
        }
      />
      <Card className="grid gap-3 p-4 md:grid-cols-[1fr_220px]">
        <Input
          aria-label="搜索班级"
          placeholder="搜索班级名称"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <Select
          aria-label="状态筛选"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="active">活跃班级</option>
          <option value="archived">已归档</option>
        </Select>
      </Card>
      {loading ? (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((x) => (
            <Card key={x} className="p-5">
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="mt-4 h-20" />
            </Card>
          ))}
        </section>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="还没有班级"
          description="创建第一个班级后，即可添加或导入学生。"
        />
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <Card key={item.id} className="p-5">
              <div className="flex items-start justify-between">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--brand-50)] text-[var(--brand-700)]">
                  <Icon name="classes" />
                </span>
                <span className="text-xs text-[var(--text-secondary)]">
                  {item.status === "active" ? "活跃" : "已归档"}
                </span>
              </div>
              <Link
                href={`/classes/${item.id}`}
                className="mt-5 block text-lg font-bold hover:text-[var(--brand-700)]"
              >
                {item.name}
              </Link>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                {[item.grade, item.subject].filter(Boolean).join(" · ") ||
                  "未填写年级与学科"}
              </p>
              <div className="mt-5 grid grid-cols-2 border-t border-[var(--border)] pt-4 text-sm">
                <span>{item.active_student_count} 名学生</span>
                <span>{item.group_count} 个分组</span>
              </div>
              <div className="mt-4 flex gap-2">
                <Link href={`/classes/${item.id}`}>
                  <Button variant="outline">查看班级</Button>
                </Link>
                <Button variant="ghost" onClick={() => void changeStatus(item)}>
                  {item.status === "active" ? "归档" : "恢复"}
                </Button>
              </div>
            </Card>
          ))}
        </section>
      )}
      <div className="flex items-center justify-end gap-3">
        <Button
          variant="outline"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </Button>
        <span className="text-sm">
          {page} / {pages}
        </span>
        <Button
          variant="outline"
          disabled={page >= pages}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </Button>
      </div>
    </div>
  );
}
