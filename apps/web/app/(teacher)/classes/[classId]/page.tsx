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
  type ClassResource,
  type Group,
  type ImportPreview,
  type Student,
  type StudentAccountCandidate,
} from "@/lib/api";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

export default function ClassDetailPage({
  params,
}: {
  params: Promise<{ classId: string }>;
}) {
  const { classId } = use(params);
  const [klass, setClass] = useState<ClassRecord>();
  const [students, setStudents] = useState<Student[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [resources, setResources] = useState<ClassResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [membership, setMembership] = useState("active");
  const [groupId, setGroupId] = useState("");
  const [saving, setSaving] = useState(false);
  const [studentOpen, setStudentOpen] = useState(false);
  const [accountStudent, setAccountStudent] = useState<Student>();
  const [accountSearch, setAccountSearch] = useState("");
  const [accountCandidates, setAccountCandidates] = useState<
    StudentAccountCandidate[]
  >([]);
  const [accountCandidateId, setAccountCandidateId] = useState("");
  const [accountLoading, setAccountLoading] = useState(false);
  const [preview, setPreview] = useState<ImportPreview>();
  const toast = useToast();
  const load = async (background = false) => {
    if (!background) {
      setLoading(true);
      setError("");
    }
    try {
      const query = new URLSearchParams({ search, status: membership });
      if (groupId) query.set("group_id", groupId);
      const [c, s, g, r] = await Promise.all([
        classesApi.get(classId),
        studentsApi.list(classId, query.toString()),
        groupsApi.list(classId),
        classesApi.resources(classId),
      ]);
      setClass(c);
      setStudents(s.items);
      setGroups(g);
      setResources(r);
    } catch (e) {
      if (!background) {
        setError(e instanceof Error ? e.message : "班级详情加载失败");
      }
    } finally {
      if (!background) setLoading(false);
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [classId, search, membership, groupId]);
  useSmartRefresh(() => load(true), {
    enabled: !saving,
    intervalMs: 60_000,
  });
  useEffect(() => {
    if (!accountStudent) return;
    let current = true;
    const timer = window.setTimeout(async () => {
      setAccountLoading(true);
      try {
        const candidates = await studentsApi.accountCandidates(accountSearch);
        if (current) {
          setAccountCandidates(candidates);
          setAccountCandidateId((selected) =>
            candidates.some((candidate) => candidate.id === selected)
              ? selected
              : "",
          );
        }
      } catch (error) {
        if (current) {
          setAccountCandidates([]);
          toast(
            error instanceof ApiError ? error.body.message : "学生账号加载失败",
            "error",
          );
        }
      } finally {
        if (current) setAccountLoading(false);
      }
    }, 200);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [accountSearch, accountStudent?.id]);
  const addStudent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await studentsApi.add(classId, {
        name: String(form.get("name")),
        student_number: String(form.get("student_number")),
        email: String(form.get("email") || "") || undefined,
      });
      formElement.reset();
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
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await groupsApi.create(classId, {
        name: String(form.get("name")),
        description: String(form.get("description") || ""),
      });
      formElement.reset();
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
  const uploadResource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const file = form.get("resource_file");
    if (!(file instanceof File) || !file.size) return;
    setSaving(true);
    try {
      await classesApi.uploadResource(classId, file, {
        title: String(form.get("resource_title") || "") || undefined,
        resource_type: String(
          form.get("resource_type") || "exercise",
        ) as ClassResource["resource_type"],
      });
      formElement.reset();
      await load(true);
      toast("资料已整理并保存");
    } catch (error) {
      toast(
        error instanceof ApiError ? error.message : "资料保存失败",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };
  const setResourcePublication = async (resource: ClassResource) => {
    setSaving(true);
    try {
      await classesApi.setResourcePublication(
        classId,
        resource.id,
        !resource.student_visible,
      );
      await load(true);
      toast(
        resource.student_visible ? "已停止向学生发布" : "资料已向本班学生发布",
      );
    } catch (error) {
      toast(
        error instanceof ApiError ? error.message : "资料发布状态更新失败",
        "error",
      );
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
  const openAccountLink = (student: Student) => {
    setAccountStudent(student);
    setAccountSearch("");
    setAccountCandidates([]);
    setAccountCandidateId("");
  };
  const linkAccount = async () => {
    if (!accountStudent || !accountCandidateId) return;
    setSaving(true);
    try {
      await studentsApi.linkAccount(accountStudent.id, accountCandidateId);
      toast("学生登录账号已关联");
      await load();
      setAccountStudent(undefined);
    } catch (e) {
      toast(e instanceof ApiError ? e.body.message : "账号关联失败", "error");
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
                <Input
                  name="email"
                  type="email"
                  label="联系邮箱（可选）"
                  description="仅作为联系信息，不用于关联登录账号"
                />
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
      <Dialog
        title="关联学生登录账号"
        description={
          accountStudent
            ? `为 ${accountStudent.name}（学号 ${accountStudent.student_number}）选择登录账号。邮箱不参与关联。`
            : undefined
        }
        open={!!accountStudent}
        onOpenChange={(open) => {
          if (!open) setAccountStudent(undefined);
        }}
        trigger={<span className="hidden" />}
      >
        <div className="grid gap-4">
          <Input
            label="搜索学生账号"
            placeholder="输入用户名或账号姓名"
            value={accountSearch}
            onChange={(event) => setAccountSearch(event.target.value)}
          />
          <div
            className="max-h-64 space-y-2 overflow-y-auto"
            aria-label="可关联的学生账号"
          >
            {accountLoading ? (
              <p className="text-sm text-[var(--text-secondary)]">
                正在加载账号…
              </p>
            ) : accountCandidates.length ? (
              accountCandidates.map((candidate) => (
                <label
                  className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 hover:bg-slate-50"
                  key={candidate.id}
                >
                  <input
                    type="radio"
                    name="student_account"
                    value={candidate.id}
                    checked={accountCandidateId === candidate.id}
                    onChange={() => setAccountCandidateId(candidate.id)}
                  />
                  <span className="grid text-sm">
                    <span className="font-semibold">
                      {candidate.display_name}
                    </span>
                    <span className="text-[var(--text-secondary)]">
                      用户名：{candidate.username}
                    </span>
                  </span>
                </label>
              ))
            ) : (
              <p className="rounded-lg bg-slate-50 p-3 text-sm text-[var(--text-secondary)]">
                没有可关联的启用学生账号。请先让管理员创建学生账号，或调整搜索词。
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              onClick={() => setAccountStudent(undefined)}
            >
              取消
            </Button>
            <Button
              disabled={!accountCandidateId}
              loading={saving}
              onClick={() => void linkAccount()}
            >
              确认关联
            </Button>
          </div>
        </div>
      </Dialog>
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
      <Card className="space-y-4 p-5">
        <div>
          <h2 className="font-bold">班级资料</h2>
          <p className="mt-1 text-sm text-slate-600">
            保存常用习题、讲义或参考资料；创建本班作业时可以直接选择。
          </p>
        </div>
        <form
          className="grid gap-3 md:grid-cols-[1fr_180px_1fr_auto]"
          onSubmit={uploadResource}
        >
          <Input
            name="resource_file"
            type="file"
            accept=".pdf,.png,.jpg,.jpeg"
            aria-label="选择班级资料文件"
            required
          />
          <Select
            name="resource_type"
            aria-label="资料类型"
            defaultValue="exercise"
          >
            <option value="exercise">习题</option>
            <option value="handout">讲义</option>
            <option value="reference">参考资料</option>
            <option value="other">其他</option>
          </Select>
          <Input
            name="resource_title"
            aria-label="资料名称（可选）"
            placeholder="资料名称（可选）"
          />
          <Button loading={saving} disabled={klass.status !== "active"}>
            添加资料
          </Button>
        </form>
        {resources.length ? (
          <ul className="divide-y rounded-xl border border-slate-200 px-4">
            {resources.map((resource) => (
              <li
                className="flex flex-wrap justify-between gap-2 py-3"
                key={resource.id}
              >
                <span>
                  <strong>{resource.title}</strong>
                  <span className="ml-2 text-sm text-slate-600">
                    {resource.file_name} · {resource.page_count} 页
                  </span>
                </span>
                <span className="flex items-center gap-2 text-sm text-slate-500">
                  {
                    {
                      exercise: "习题",
                      handout: "讲义",
                      reference: "参考资料",
                      other: "其他",
                    }[resource.resource_type]
                  }
                  <Button
                    variant="outline"
                    disabled={saving}
                    onClick={() => void setResourcePublication(resource)}
                  >
                    {resource.student_visible ? "停止发布" : "发布给学生"}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">暂时没有班级资料。</p>
        )}
      </Card>
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
                <th>学生端</th>
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
                    {student.account_linked ? (
                      <span className="grid text-sm text-emerald-700">
                        <span>已关联</span>
                        {student.linked_account?.username && (
                          <span className="text-xs text-[var(--text-secondary)]">
                            {student.linked_account.username}
                          </span>
                        )}
                      </span>
                    ) : (
                      <Button
                        variant="ghost"
                        disabled={saving}
                        onClick={() => openAccountLink(student)}
                      >
                        关联账号
                      </Button>
                    )}
                  </td>
                  <td>
                    {new Date(student.joined_at).toLocaleDateString("zh-CN")}
                  </td>
                  <td>
                    <Button
                      variant="ghost"
                      onClick={() => void remove(student)}
                      disabled={
                        saving || student.membership_status !== "active"
                      }
                    >
                      移出
                    </Button>
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
