"use client";
/* eslint-disable react-hooks/exhaustive-deps */
import Link from "next/link";
import { use, useEffect, useState, type FormEvent } from "react";
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
  StatCard,
  Table,
  useToast,
} from "@/components/ui";
import {
  ApiError,
  classesApi,
  groupsApi,
  importsApi,
  studentsApi,
  type ClassRecord,
  type Group,
  type ImportPreview,
  type Student,
} from "@/lib/api";
import { StudentAccountDialog } from "@/components/student-account-dialog";

export default function ClassDetailPage({
  params,
}: {
  params: Promise<{ classId: string }>;
}) {
  const { classId } = use(params);
  const [klass, setClass] = useState<ClassRecord>();
  const [students, setStudents] = useState<Student[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [membership, setMembership] = useState("active");
  const [groupId, setGroupId] = useState("");
  const [saving, setSaving] = useState(false);
  const [studentOpen, setStudentOpen] = useState(false);
  const [preview, setPreview] = useState<ImportPreview>();
  const toast = useToast();
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ search, status: membership });
      if (groupId) query.set("group_id", groupId);
      const [c, s, g] = await Promise.all([
        classesApi.get(classId),
        studentsApi.list(classId, query.toString()),
        groupsApi.list(classId),
      ]);
      setClass(c);
      setStudents(s.items);
      setGroups(g);
    } catch (e) {
      setError(e instanceof Error ? e.message : "班级详情加载失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [classId, search, membership, groupId]);
  const addStudent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    const form = new FormData(event.currentTarget);
    try {
      await studentsApi.add(classId, {
        name: String(form.get("name")),
        student_number: String(form.get("student_number")),
        email: String(form.get("email") || "") || undefined,
      });
      event.currentTarget.reset();
      toast("学生已加入班级");
      await load();
      setStudentOpen(false);
    } catch (e) {
      toast(e instanceof ApiError ? e.body.message : "添加失败", "error");
    } finally {
      setSaving(false);
    }
  };
  const addGroup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    const form = new FormData(event.currentTarget);
    try {
      await groupsApi.create(classId, {
        name: String(form.get("name")),
        description: String(form.get("description") || ""),
      });
      event.currentTarget.reset();
      toast("分组已创建");
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "创建失败", "error");
    } finally {
      setSaving(false);
    }
  };
  const upload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const file = new FormData(event.currentTarget).get("file");
    if (!(file instanceof File) || !file.size) return;
    setSaving(true);
    try {
      setPreview(await importsApi.preview(classId, file));
    } catch (e) {
      toast(e instanceof ApiError ? e.body.message : "文件解析失败", "error");
    } finally {
      setSaving(false);
    }
  };
  const confirm = async () => {
    if (!preview) return;
    setSaving(true);
    try {
      const result = await importsApi.confirm(preview.id);
      setPreview(result);
      toast("有效学生已导入");
      await load();
    } catch (e) {
      toast(
        e instanceof ApiError
          ? e.body.message
          : e instanceof Error
            ? e.message
            : "确认导入失败",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };
  const remove = async (student: Student) => {
    if (
      !window.confirm(
        `将 ${student.name} 移出班级？学生档案不会删除，且会移出本班分组。`,
      )
    )
      return;
    setSaving(true);
    try {
      await studentsApi.remove(classId, student.id);
      toast("学生已移出班级");
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "操作失败", "error");
    } finally {
      setSaving(false);
    }
  };
  if (loading && !klass)
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-80" />
      </div>
    );
  if (error && !klass)
    return <ErrorState description={error} retry={() => void load()} />;
  if (!klass) return null;
  return (
    <div className="space-y-6">
      <div>
        <Link href="/classes" className="text-sm text-[var(--brand-700)]">
          ← 返回班级列表
        </Link>
      </div>
      <PageHeader
        title={klass.name}
        description={
          [klass.grade, klass.subject, klass.academic_year, klass.semester]
            .filter(Boolean)
            .join(" · ") || "尚未填写班级信息"
        }
        actions={
          <>
            <Dialog
              title="添加学生"
              description="若该学号已属于当前教师，将复用学生档案并加入本班。"
              dismissible={false}
              open={studentOpen}
              onOpenChange={setStudentOpen}
              trigger={<Button>添加学生</Button>}
            >
              <form className="grid gap-4" onSubmit={addStudent}>
                <Input name="name" label="姓名" required />
                <Input
                  name="student_number"
                  label="学号"
                  description="按字符串保存，保留前导零"
                  required
                />
                <Input name="email" type="email" label="邮箱（可选）" />
                <Button loading={saving}>确认添加</Button>
              </form>
            </Dialog>
            <Dialog
              title="创建分组"
              dismissible={false}
              trigger={<Button variant="outline">管理分组</Button>}
            >
              <form className="grid gap-4" onSubmit={addGroup}>
                <Input name="name" label="分组名称" required />
                <Input name="description" label="说明（可选）" />
                <Button loading={saving}>创建分组</Button>
              </form>
              <div className="mt-5 space-y-2">
                {groups.map((g) => (
                  <Card
                    className="flex items-center justify-between p-3"
                    key={g.id}
                  >
                    <span>
                      {g.name}（{g.member_count}）
                    </span>
                    <Button
                      variant="ghost"
                      disabled={saving}
                      onClick={async () => {
                        if (
                          window.confirm("删除分组不会删除学生，是否继续？")
                        ) {
                          setSaving(true);
                          try {
                            await groupsApi.remove(g.id);
                            await load();
                            toast("分组已删除");
                          } catch (error) {
                            toast(
                              error instanceof ApiError
                                ? error.message
                                : "删除分组失败",
                              "error",
                            );
                          } finally {
                            setSaving(false);
                          }
                        }
                      }}
                    >
                      删除
                    </Button>
                  </Card>
                ))}
              </div>
            </Dialog>
            <Dialog
              title="导入学生名单"
              description="支持 XLSX/UTF-8 CSV。先预览校验，再确认写入。"
              dismissible={false}
              trigger={<Button variant="outline">导入学生</Button>}
            >
              <div className="space-y-4">
                <a
                  className="text-sm text-[var(--brand-700)] underline"
                  href={importsApi.templateUrl()}
                >
                  下载 XLSX 模板
                </a>
                <form className="grid gap-3" onSubmit={upload}>
                  <Input name="file" type="file" accept=".xlsx,.csv" required />
                  <Button loading={saving}>上传并预览</Button>
                </form>
                {preview && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-4 gap-2 text-center text-xs">
                      <Card className="p-2">总计 {preview.total_rows}</Card>
                      <Card className="p-2">可导入 {preview.valid_rows}</Card>
                      <Card className="p-2">错误 {preview.invalid_rows}</Card>
                      <Card className="p-2">重复 {preview.duplicate_rows}</Card>
                    </div>
                    <div className="max-h-64 overflow-auto">
                      <Table>
                        <thead>
                          <tr>
                            <th>行</th>
                            <th>姓名</th>
                            <th>学号</th>
                            <th>状态/错误</th>
                          </tr>
                        </thead>
                        <tbody>
                          {preview.rows.map((row) => (
                            <tr className="border-t" key={row.row_number}>
                              <td>{row.row_number}</td>
                              <td>{row.data.name}</td>
                              <td>{row.data.student_number}</td>
                              <td>
                                {row.errors.map((e) => e.message).join("；") ||
                                  "可导入"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </Table>
                    </div>
                    {preview.status === "confirmed" ? (
                      <p className="text-sm text-emerald-700">
                        导入完成：新建 {preview.result.created ?? 0}，加入班级{" "}
                        {preview.result.joined ?? 0}，跳过{" "}
                        {preview.result.skipped ?? 0}。
                      </p>
                    ) : (
                      <>
                        {(preview.invalid_rows > 0 ||
                          preview.duplicate_rows > 0) && (
                          <p
                            className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                            role="alert"
                          >
                            当前预览包含错误或重复行，整批不会写入。请修正文件后重新上传预览。
                          </p>
                        )}
                        <Button
                          loading={saving}
                          disabled={
                            preview.valid_rows === 0 ||
                            preview.invalid_rows > 0 ||
                            preview.duplicate_rows > 0
                          }
                          onClick={() => void confirm()}
                        >
                          确认导入整批合法数据
                        </Button>
                      </>
                    )}
                  </div>
                )}
              </div>
            </Dialog>
          </>
        }
      />
      {klass.status === "archived" && (
        <Card className="border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          该班级已归档，不能用于创建新作业；学生与历史关系均保留。
        </Card>
      )}
      <section className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="班级学生"
          value={String(klass.active_student_count)}
          note="当前有效成员"
        />
        <StatCard
          label="学生分组"
          value={String(klass.group_count)}
          note="按本班独立管理"
        />
        <StatCard label="作业历史" value="—" note="将在创建作业阶段接入" />
      </section>
      <Card className="grid gap-3 p-4 md:grid-cols-[1fr_180px_180px]">
        <Input
          aria-label="搜索学生"
          placeholder="搜索姓名或学号"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select
          aria-label="关系状态"
          value={membership}
          onChange={(e) => setMembership(e.target.value)}
        >
          <option value="active">在班</option>
          <option value="removed">已移出</option>
        </Select>
        <Select
          aria-label="分组筛选"
          value={groupId}
          onChange={(e) => setGroupId(e.target.value)}
        >
          <option value="">全部分组</option>
          {groups.map((g) => (
            <option value={g.id} key={g.id}>
              {g.name}
            </option>
          ))}
        </Select>
      </Card>
      {error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : students.length === 0 ? (
        <EmptyState
          title="没有符合条件的学生"
          description="可手动添加学生，或上传名单批量导入。"
        />
      ) : (
        <Card className="p-2">
          <Table>
            <thead>
              <tr className="text-[var(--text-secondary)]">
                <th className="p-3">姓名</th>
                <th>学号</th>
                <th>分组</th>
                <th>状态</th>
                <th>加入时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {students.map((student) => (
                <tr
                  className="border-t border-[var(--border)]"
                  key={student.id}
                >
                  <td className="p-3 font-semibold">{student.name}</td>
                  <td>{student.student_number}</td>
                  <td>
                    {student.groups.map((g) => g.name).join("、") || "未分组"}
                  </td>
                  <td>
                    {student.membership_status === "active" ? "在班" : "已移出"}
                  </td>
                  <td>
                    {new Date(student.joined_at).toLocaleDateString("zh-CN")}
                  </td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {student.membership_status === "active" &&
                        (student.account_link ? (
                          <span className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
                            <strong className="block text-slate-800">
                              {student.account_link.status === "active"
                                ? "账号已开通"
                                : "账号已停用"}
                            </strong>
                            学号 {student.account_link.login_name || "待处理"} ·
                            {!student.account_link.recovery_email
                              ? "安全邮箱未设置"
                              : student.account_link.recovery_email_verified
                                ? "安全邮箱已验证"
                                : "安全邮箱待验证"}
                          </span>
                        ) : (
                          <StudentAccountDialog student={student} />
                        ))}
                      <Button
                        variant="ghost"
                        onClick={() => void remove(student)}
                        disabled={
                          saving || student.membership_status !== "active"
                        }
                      >
                        移出
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      <EmptyState
        title="暂无作业历史"
        description="学生作业与成绩将在第四部分“创建作业与评分标准”接入，本页不展示虚假成绩。"
      />
    </div>
  );
}
