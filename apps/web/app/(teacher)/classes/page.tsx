"use client";
/* eslint-disable react-hooks/exhaustive-deps */
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
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
import { useSmartRefresh } from "@/lib/use-smart-refresh";

export default function ClassesPage() {
  const academicYearRef = useRef<HTMLSelectElement>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [items, setItems] = useState<ClassRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("active");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const openAcademicYearPicker = () => {
    const picker = academicYearRef.current;
    if (!picker) return;
    picker.focus();
    try {
      picker.showPicker?.();
    } catch {
      // Native select remains focused when the browser blocks programmatic opening.
    }
  };
  const load = async (background = false) => {
    if (!background) {
      setLoading(true);
      setError("");
    }
    try {
      const q = new URLSearchParams({ page: String(page), status, search });
      const data = await classesApi.list(q.toString());
      setItems(data.items);
      setPages(Math.max(data.pages, 1));
    } catch (e) {
      if (!background) {
        setError(e instanceof Error ? e.message : "班级列表加载失败");
      }
    } finally {
      if (!background) setLoading(false);
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => window.clearTimeout(timer);
  }, [search, status, page]);
  useSmartRefresh(() => load(true), { intervalMs: 60_000 });
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const subject = String(form.get("subject") || "").trim();
    if (!subject || subject === "数学") {
      toast("请填写具体大学课程，例如数学分析或线性代数", "error");
      setSaving(false);
      return;
    }
    try {
      await classesApi.create({
        name: String(form.get("name")),
        grade: String(form.get("grade") || ""),
        subject,
        academic_year: String(form.get("academic_year") || ""),
        semester: String(form.get("semester") || ""),
      });
      formElement.reset();
      toast("班级已创建");
      await load();
      setCreateOpen(false);
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
            dismissible={false}
            open={createOpen}
            onOpenChange={setCreateOpen}
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
                <Input
                  label="大学课程"
                  name="subject"
                  list="class-course-options"
                  placeholder="如：数学分析、线性代数"
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      openAcademicYearPicker();
                    }
                  }}
                />
                <datalist id="class-course-options">
                  <option value="数学分析" />
                  <option value="线性代数" />
                  <option value="高等代数" />
                  <option value="概率论" />
                  <option value="常微分方程" />
                </datalist>
                <Select
                  ref={academicYearRef}
                  label="学年"
                  name="academic_year"
                  defaultValue=""
                  onFocus={(event) => {
                    if (event.currentTarget.matches(":focus-visible")) {
                      try {
                        event.currentTarget.showPicker?.();
                      } catch {
                        // Keyboard users can still open the focused native select.
                      }
                    }
                  }}
                >
                  <option value="" disabled>
                    请选择学年
                  </option>
                  <option value="2024-2025">2024-2025</option>
                  <option value="2025-2026">2025-2026</option>
                  <option value="2026-2027">2026-2027</option>
                  <option value="2027-2028">2027-2028</option>
                  <option value="2028-2029">2028-2029</option>
                </Select>
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
