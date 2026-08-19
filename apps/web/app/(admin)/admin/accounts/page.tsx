"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useAuthUser } from "@/components/auth-gate";
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
  Table,
  useToast,
} from "@/components/ui";
import {
  adminAccountsApi,
  ApiError,
  type AccountAuditList,
  type AccountList,
  type AccountSecurityOverview,
  type AccountType,
  type BulkAccountAction,
  type ManagedAccount,
} from "@/lib/api";
import {
  ACCOUNT_CSV_TEMPLATE,
  parseAccountCsv,
  type CsvAccountPreview,
} from "@/lib/account-csv";

const typeLabels: Record<AccountType, string> = {
  teacher: "教师",
  student: "学生",
  admin: "管理员",
};
const actionLabels: Record<string, string> = {
  "admin.account.create": "创建账号",
  "admin.account.bulk_create": "批量导入",
  "admin.account.update": "更新账号",
  "admin.account.password_reset": "重置密码",
  "admin.account.bulk_activate": "批量启用",
  "admin.account.bulk_deactivate": "批量停用",
  "admin.account.bulk_revoke_sessions": "批量强制下线",
  "admin.account.bulk_action": "批量操作汇总",
  "admin.account.session_revoke": "撤销会话",
};

function errorMessage(reason: unknown) {
  return reason instanceof ApiError ? reason.message : "操作失败，请稍后重试";
}

function formatTime(value: string | null) {
  if (!value) return "从未登录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function AdminAccountsPage() {
  const currentUser = useAuthUser();
  const toast = useToast();
  const [data, setData] = useState<AccountList | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [accountType, setAccountType] = useState<AccountType | "">("");
  const [status, setStatus] = useState<"active" | "inactive" | "">("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ManagedAccount | null>(null);
  const [resetTarget, setResetTarget] = useState<ManagedAccount | null>(null);
  const [busyId, setBusyId] = useState("");
  const [offset, setOffset] = useState(0);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [csvName, setCsvName] = useState("");
  const [csvPreview, setCsvPreview] = useState<CsvAccountPreview | null>(null);
  const [serverImportErrors, setServerImportErrors] = useState<
    Array<{ row_number: number; username: string; message: string }>
  >([]);
  const [auditData, setAuditData] = useState<AccountAuditList | null>(null);
  const [auditError, setAuditError] = useState("");
  const [auditOffset, setAuditOffset] = useState(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [bulkActionErrors, setBulkActionErrors] = useState<
    Array<{ account_id: string; username?: string; message: string }>
  >([]);
  const [securityData, setSecurityData] =
    useState<AccountSecurityOverview | null>(null);
  const [securityError, setSecurityError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const result = await adminAccountsApi.list({
        query,
        account_type: accountType,
        status,
        offset,
      });
      setData(result);
      setSelectedIds((current) =>
        current.filter((id) => result.items.some((item) => item.id === id)),
      );
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, [accountType, offset, query, status]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadAudit = useCallback(async () => {
    setAuditError("");
    try {
      setAuditData(await adminAccountsApi.audit(auditOffset));
    } catch (reason) {
      setAuditError(errorMessage(reason));
    }
  }, [auditOffset]);

  useEffect(() => {
    void loadAudit();
  }, [loadAudit]);

  const loadSecurity = useCallback(async () => {
    setSecurityError("");
    try {
      setSecurityData(await adminAccountsApi.security());
    } catch (reason) {
      setSecurityError(errorMessage(reason));
    }
  }, []);

  useEffect(() => {
    void loadSecurity();
  }, [loadSecurity]);

  function changeBulkOpen(open: boolean) {
    setBulkOpen(open);
    if (!open) {
      setCsvName("");
      setCsvPreview(null);
      setServerImportErrors([]);
    }
  }

  async function previewCsv(file: File | undefined) {
    if (!file) return;
    setCsvName(file.name);
    setServerImportErrors([]);
    try {
      setCsvPreview(parseAccountCsv(await file.text()));
    } catch {
      setCsvPreview({ rows: [], file_errors: ["无法读取 CSV 文件"] });
    }
  }

  function downloadTemplate() {
    const blob = new Blob(["\uFEFF", ACCOUNT_CSV_TEMPLATE], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "ahamark-account-import-template.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function importCsvAccounts() {
    if (!csvPreview) return;
    const validRows = csvPreview.rows.filter(
      (row) => row.errors.length === 0 && row.account_type,
    );
    if (!validRows.length) {
      toast("没有可导入的有效账号", "error");
      return;
    }
    setBusyId("bulk");
    try {
      const result = await adminAccountsApi.bulkCreate(
        validRows.map((row) => ({
          username: row.username,
          display_name: row.display_name,
          password: row.password,
          account_type: row.account_type as "teacher" | "student",
        })),
      );
      setServerImportErrors(result.errors);
      toast(
        result.errors.length
          ? `已创建 ${result.created.length} 个账号，${result.errors.length} 行未导入`
          : `已成功创建 ${result.created.length} 个账号`,
        result.errors.length ? "error" : "success",
      );
      await Promise.all([load(), loadAudit()]);
      if (!result.errors.length) changeBulkOpen(false);
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const password = String(values.get("password"));
    if (password !== String(values.get("password_confirmation"))) {
      toast("两次输入的密码不一致", "error");
      return;
    }
    setBusyId("create");
    try {
      await adminAccountsApi.create({
        username: String(values.get("username")),
        display_name: String(values.get("display_name")),
        password,
        account_type: String(values.get("account_type")) as AccountType,
      });
      form.reset();
      setCreateOpen(false);
      toast("账号已创建，可立即使用用户名登录");
      await load();
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  async function toggleStatus(account: ManagedAccount) {
    const nextStatus = account.status === "active" ? "inactive" : "active";
    if (
      nextStatus === "inactive" &&
      !window.confirm(
        `停用 ${account.display_name} 后，其所有登录会话会立即失效。继续吗？`,
      )
    )
      return;
    setBusyId(account.id);
    try {
      await adminAccountsApi.update(account.id, { status: nextStatus });
      toast(
        nextStatus === "active" ? "账号已启用" : "账号已停用并退出所有设备",
      );
      await load();
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  async function updateDisplayName(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editTarget) return;
    const values = new FormData(event.currentTarget);
    setBusyId(`edit-${editTarget.id}`);
    try {
      await adminAccountsApi.update(editTarget.id, {
        display_name: String(values.get("display_name")),
      });
      setEditTarget(null);
      toast("姓名已更新");
      await load();
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  async function resetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!resetTarget) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const password = String(values.get("password"));
    if (password !== String(values.get("password_confirmation"))) {
      toast("两次输入的密码不一致", "error");
      return;
    }
    setBusyId(`reset-${resetTarget.id}`);
    try {
      const result = await adminAccountsApi.resetPassword(
        resetTarget.id,
        password,
      );
      setResetTarget(null);
      toast(`密码已重置，${result.sessions_revoked} 个会话已退出`);
      await load();
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  function toggleSelected(id: string) {
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((candidate) => candidate !== id)
        : [...current, id],
    );
  }

  async function runBulkAction(action: BulkAccountAction) {
    if (!selectedIds.length) return;
    const descriptions: Record<BulkAccountAction, string> = {
      activate: `启用选中的 ${selectedIds.length} 个账号`,
      deactivate: `停用选中的 ${selectedIds.length} 个账号并撤销其全部会话`,
      revoke_sessions: `强制选中的 ${selectedIds.length} 个账号退出所有设备`,
    };
    if (
      !window.confirm(`${descriptions[action]}。此操作会写入审计记录，继续吗？`)
    )
      return;
    setBusyId(`bulk-${action}`);
    setBulkActionErrors([]);
    try {
      const result = await adminAccountsApi.bulkAction(selectedIds, action);
      setBulkActionErrors(result.errors);
      const sessionsRevoked = result.processed.reduce(
        (total, item) => total + item.sessions_revoked,
        0,
      );
      toast(
        result.errors.length
          ? `已处理 ${result.processed.length} 个账号，${result.errors.length} 个失败`
          : `已处理 ${result.processed.length} 个账号，撤销 ${sessionsRevoked} 个会话`,
        result.errors.length ? "error" : "success",
      );
      setSelectedIds([]);
      await Promise.all([load(), loadAudit(), loadSecurity()]);
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  function exportAccounts() {
    const anchor = document.createElement("a");
    anchor.href = adminAccountsApi.exportUrl({
      query,
      account_type: accountType,
      status,
    });
    anchor.download = "ahamark-accounts.csv";
    anchor.click();
  }

  async function revokeSession(sessionId: string, username: string) {
    if (!window.confirm(`撤销 ${username} 的这个登录会话？`)) return;
    setBusyId(`session-${sessionId}`);
    try {
      await adminAccountsApi.revokeSession(sessionId);
      toast("会话已撤销，该设备需要重新登录");
      await Promise.all([load(), loadAudit(), loadSecurity()]);
    } catch (reason) {
      toast(errorMessage(reason), "error");
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        title="账号管理"
        description="统一发放教师、学生与管理员账号。停用或重置密码后，原有登录会话会立即失效。"
        actions={
          <>
            <Dialog
              open={bulkOpen}
              onOpenChange={changeBulkOpen}
              trigger={<Button variant="outline">批量导入</Button>}
              title="批量导入教师和学生"
              description="先在本地预览并检查每一行；管理员账号只能逐个创建。密码不会显示在预览或审计中。"
            >
              <div className="grid gap-4">
                <div className="flex flex-wrap items-end gap-3">
                  <label className="grid flex-1 gap-1.5 text-sm font-medium">
                    选择 CSV 文件
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      className="h-10 rounded-lg border bg-white px-3 py-2 text-sm"
                      onChange={(event) =>
                        void previewCsv(event.target.files?.[0])
                      }
                    />
                  </label>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={downloadTemplate}
                  >
                    下载模板
                  </Button>
                </div>
                {csvName && (
                  <p className="text-xs text-slate-500">文件：{csvName}</p>
                )}
                {csvPreview?.file_errors.map((message) => (
                  <p
                    key={message}
                    role="alert"
                    className="rounded-lg bg-red-50 p-3 text-sm text-red-700"
                  >
                    {message}
                  </p>
                ))}
                {csvPreview && csvPreview.rows.length > 0 && (
                  <div className="max-h-72 overflow-auto rounded-lg border">
                    <table className="w-full min-w-[560px] text-left text-xs">
                      <thead className="sticky top-0 bg-slate-50 text-slate-500">
                        <tr>
                          <th className="px-3 py-2">行</th>
                          <th className="px-3 py-2">用户名</th>
                          <th className="px-3 py-2">姓名</th>
                          <th className="px-3 py-2">类型</th>
                          <th className="px-3 py-2">检查结果</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {csvPreview.rows.map((row) => (
                          <tr key={row.row_number}>
                            <td className="px-3 py-2">{row.row_number}</td>
                            <td className="px-3 py-2 font-medium">
                              {row.username || "—"}
                            </td>
                            <td className="px-3 py-2">
                              {row.display_name || "—"}
                            </td>
                            <td className="px-3 py-2">
                              {row.account_type
                                ? typeLabels[row.account_type]
                                : "—"}
                            </td>
                            <td
                              className={`px-3 py-2 ${row.errors.length ? "text-red-700" : "text-emerald-700"}`}
                            >
                              {row.errors.join("；") || "可以导入"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {serverImportErrors.length > 0 && (
                  <div
                    role="alert"
                    className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800"
                  >
                    {serverImportErrors.map((item) => (
                      <p key={`${item.row_number}-${item.username}`}>
                        第 {item.row_number} 行 {item.username || "—"}：
                        {item.message}
                      </p>
                    ))}
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => changeBulkOpen(false)}
                  >
                    取消
                  </Button>
                  <Button
                    type="button"
                    loading={busyId === "bulk"}
                    disabled={
                      !csvPreview?.rows.some((row) => row.errors.length === 0)
                    }
                    onClick={() => void importCsvAccounts()}
                  >
                    导入有效账号
                  </Button>
                </div>
              </div>
            </Dialog>
            <Dialog
              open={createOpen}
              onOpenChange={setCreateOpen}
              trigger={<Button>创建账号</Button>}
              title="创建新账号"
              description="账号类型创建后不可直接变更，避免错误继承业务权限。"
            >
              <form className="grid gap-4" onSubmit={createAccount}>
                <Select
                  label="账号类型"
                  name="account_type"
                  defaultValue="teacher"
                  required
                >
                  <option value="teacher">教师账号</option>
                  <option value="student">学生账号</option>
                  <option value="admin">管理员账号</option>
                </Select>
                <Input
                  label="姓名"
                  name="display_name"
                  maxLength={120}
                  required
                />
                <Input
                  label="用户名"
                  name="username"
                  minLength={3}
                  maxLength={64}
                  pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}"
                  description="3–64 位字母、数字、点、下划线或连字符；保存后统一转为小写。"
                  required
                />
                <div className="grid gap-4 sm:grid-cols-2">
                  <Input
                    label="初始密码"
                    name="password"
                    type="password"
                    minLength={8}
                    required
                  />
                  <Input
                    label="确认密码"
                    name="password_confirmation"
                    type="password"
                    minLength={8}
                    required
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setCreateOpen(false)}
                  >
                    取消
                  </Button>
                  <Button type="submit" loading={busyId === "create"}>
                    创建并启用
                  </Button>
                </div>
              </form>
            </Dialog>
          </>
        }
      />

      <section aria-label="账号概览" className="grid gap-3 sm:grid-cols-3">
        {(["teacher", "student", "admin"] as AccountType[]).map((type) => (
          <Card key={type} className="p-5">
            <p className="text-sm text-slate-500">{typeLabels[type]}账号</p>
            <p className="mt-2 text-3xl font-bold">
              {data?.summary[type].total ?? "—"}
            </p>
            <p className="mt-1 text-xs text-emerald-700">
              {data ? `${data.summary[type].active} 个已启用` : "正在统计"}
            </p>
          </Card>
        ))}
      </section>

      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-bold">账号安全</h2>
            <p className="mt-1 text-sm text-slate-500">
              统计已识别账号的失败登录、长期未登录账号和当前活动会话。
            </p>
          </div>
          <Button variant="ghost" onClick={() => void loadSecurity()}>
            刷新
          </Button>
        </div>
        {securityError ? (
          <div className="mt-4">
            <ErrorState
              description={securityError}
              retry={() => void loadSecurity()}
            />
          </div>
        ) : !securityData ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-20" />
            ))}
          </div>
        ) : (
          <>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                ["24 小时失败登录", securityData.failed_logins_24h],
                ["活动会话", securityData.active_sessions],
                ["多设备账号", securityData.accounts_with_multiple_sessions],
                ["从未登录", securityData.never_logged_in_accounts],
                ["90 天未活动", securityData.stale_accounts_90d],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="mt-1 text-2xl font-bold">{value}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 max-h-72 overflow-auto rounded-lg border">
              {securityData.sessions.length === 0 ? (
                <p className="p-4 text-sm text-slate-500">当前没有活动会话。</p>
              ) : (
                <div className="divide-y">
                  {securityData.sessions.map((session) => (
                    <div
                      key={session.id}
                      className="flex flex-col justify-between gap-2 p-3 text-sm sm:flex-row sm:items-center"
                    >
                      <div>
                        <p className="font-semibold">{session.username}</p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          最近活动 {formatTime(session.last_seen_at)} · 到期{" "}
                          {formatTime(session.expires_at)}
                        </p>
                      </div>
                      {session.is_current ? (
                        <span className="text-xs font-semibold text-emerald-700">
                          当前会话
                        </span>
                      ) : (
                        <Button
                          variant="outline"
                          loading={busyId === `session-${session.id}`}
                          onClick={() =>
                            void revokeSession(session.id, session.username)
                          }
                        >
                          撤销会话
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </Card>

      <Card className="p-4 sm:p-5">
        <form
          className="grid gap-3 sm:grid-cols-[minmax(240px,1fr)_180px_160px_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            void load();
          }}
        >
          <Input
            label="搜索"
            placeholder="用户名或姓名"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select
            label="账号类型"
            value={accountType}
            onChange={(event) => {
              setOffset(0);
              setAccountType(event.target.value as AccountType | "");
            }}
          >
            <option value="">全部类型</option>
            <option value="teacher">教师</option>
            <option value="student">学生</option>
            <option value="admin">管理员</option>
          </Select>
          <Select
            label="状态"
            value={status}
            onChange={(event) => {
              setOffset(0);
              setStatus(event.target.value as typeof status);
            }}
          >
            <option value="">全部状态</option>
            <option value="active">已启用</option>
            <option value="inactive">已停用</option>
          </Select>
          <Button className="self-end" type="submit" variant="outline">
            查询
          </Button>
        </form>
      </Card>

      <Card className="grid gap-3 p-4">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <p className="font-semibold">批量运维</p>
            <p className="mt-1 text-xs text-slate-500">
              已选择 {selectedIds.length} 个账号；当前管理员不能加入高风险操作。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="ghost" onClick={exportAccounts}>
              导出当前清单
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={!selectedIds.length || busyId.startsWith("bulk-")}
              loading={busyId === "bulk-activate"}
              onClick={() => void runBulkAction("activate")}
            >
              批量启用
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!selectedIds.length || busyId.startsWith("bulk-")}
              loading={busyId === "bulk-revoke_sessions"}
              onClick={() => void runBulkAction("revoke_sessions")}
            >
              强制下线
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!selectedIds.length || busyId.startsWith("bulk-")}
              loading={busyId === "bulk-deactivate"}
              onClick={() => void runBulkAction("deactivate")}
            >
              批量停用
            </Button>
          </div>
        </div>
        {bulkActionErrors.length > 0 && (
          <div
            role="alert"
            className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800"
          >
            <p className="font-semibold">以下账号未处理：</p>
            {bulkActionErrors.map((item) => (
              <p key={item.account_id} className="mt-1">
                {item.username ?? item.account_id}：{item.message}
              </p>
            ))}
          </div>
        )}
      </Card>

      {error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : !data ? (
        <Card className="grid gap-3 p-5">
          <Skeleton className="h-10" />
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </Card>
      ) : data.items.length === 0 ? (
        <EmptyState
          title="没有符合条件的账号"
          description="调整搜索词或筛选条件后再试。"
          icon="search"
        />
      ) : (
        <Card>
          <Table>
            <thead className="border-b bg-slate-50 text-xs text-slate-500">
              <tr>
                <th className="w-12 px-5 py-3 font-semibold">
                  <input
                    type="checkbox"
                    aria-label="选择本页账号"
                    checked={
                      data.items.some((item) => item.id !== currentUser?.id) &&
                      data.items
                        .filter((item) => item.id !== currentUser?.id)
                        .every((item) => selectedIds.includes(item.id))
                    }
                    onChange={(event) =>
                      setSelectedIds(
                        event.target.checked
                          ? data.items
                              .filter((item) => item.id !== currentUser?.id)
                              .map((item) => item.id)
                          : [],
                      )
                    }
                  />
                </th>
                <th className="px-5 py-3 font-semibold">账号</th>
                <th className="px-5 py-3 font-semibold">类型</th>
                <th className="px-5 py-3 font-semibold">状态</th>
                <th className="px-5 py-3 font-semibold">登录活动</th>
                <th className="px-5 py-3 text-right font-semibold">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.items.map((account) => {
                const isSelf = account.id === currentUser?.id;
                return (
                  <tr key={account.id} className="hover:bg-slate-50/70">
                    <td className="px-5 py-4">
                      <input
                        type="checkbox"
                        aria-label={`选择账号 ${account.username}`}
                        disabled={isSelf}
                        checked={selectedIds.includes(account.id)}
                        onChange={() => toggleSelected(account.id)}
                      />
                    </td>
                    <td className="px-5 py-4">
                      <p className="font-semibold">{account.display_name}</p>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {account.username}
                      </p>
                    </td>
                    <td className="px-5 py-4">
                      {typeLabels[account.account_type]}
                    </td>
                    <td className="px-5 py-4">
                      <span
                        className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                          account.status === "active"
                            ? "bg-emerald-50 text-emerald-700"
                            : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {account.status === "active" ? "已启用" : "已停用"}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-sm text-slate-600">
                      <p>{account.active_session_count} 个在线会话</p>
                      <p className="mt-0.5 text-xs">
                        最近：{formatTime(account.last_seen_at)}
                      </p>
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="ghost"
                          disabled={busyId === account.id}
                          onClick={() => setEditTarget(account)}
                        >
                          编辑姓名
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={isSelf || busyId === account.id}
                          onClick={() => setResetTarget(account)}
                        >
                          重置密码
                        </Button>
                        <Button
                          variant={
                            account.status === "active"
                              ? "outline"
                              : "secondary"
                          }
                          disabled={isSelf || busyId === account.id}
                          loading={busyId === account.id}
                          onClick={() => void toggleStatus(account)}
                        >
                          {account.status === "active" ? "停用" : "启用"}
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <div className="flex items-center justify-between gap-3 border-t px-5 py-3 text-xs text-slate-500">
            <p>
              第 {data.offset + 1}–
              {Math.min(data.offset + data.items.length, data.total)} 个，共{" "}
              {data.total} 个账号
            </p>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                disabled={data.offset === 0}
                onClick={() => setOffset(Math.max(0, data.offset - data.limit))}
              >
                上一页
              </Button>
              <Button
                variant="ghost"
                disabled={data.offset + data.items.length >= data.total}
                onClick={() => setOffset(data.offset + data.limit)}
              >
                下一页
              </Button>
            </div>
          </div>
        </Card>
      )}

      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-bold">最近账号操作</h2>
            <p className="mt-1 text-sm text-slate-500">
              只记录管理员账号运维动作，不保存密码。
            </p>
          </div>
          <Button variant="ghost" onClick={() => void loadAudit()}>
            刷新
          </Button>
        </div>
        {auditError ? (
          <div className="mt-4">
            <ErrorState
              description={auditError}
              retry={() => void loadAudit()}
            />
          </div>
        ) : !auditData ? (
          <div className="mt-4 grid gap-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : auditData.items.length === 0 ? (
          <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-500">
            还没有账号运维记录。
          </p>
        ) : (
          <div className="mt-4 divide-y rounded-lg border">
            {auditData.items.map((entry) => (
              <div
                key={entry.id}
                className="flex flex-col justify-between gap-2 p-3 text-sm sm:flex-row sm:items-center"
              >
                <div>
                  <span className="font-semibold">
                    {actionLabels[entry.action] ?? entry.action}
                  </span>
                  <span className="ml-2 text-slate-500">
                    {entry.actor_username}
                    {entry.target_username ? ` → ${entry.target_username}` : ""}
                  </span>
                </div>
                <time className="text-xs text-slate-500">
                  {formatTime(entry.created_at)}
                </time>
              </div>
            ))}
            <div className="flex items-center justify-between p-3 text-xs text-slate-500">
              <span>共 {auditData.total} 条</span>
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  disabled={auditData.offset === 0}
                  onClick={() =>
                    setAuditOffset(
                      Math.max(0, auditData.offset - auditData.limit),
                    )
                  }
                >
                  上一页
                </Button>
                <Button
                  variant="ghost"
                  disabled={
                    auditData.offset + auditData.items.length >= auditData.total
                  }
                  onClick={() =>
                    setAuditOffset(auditData.offset + auditData.limit)
                  }
                >
                  下一页
                </Button>
              </div>
            </div>
          </div>
        )}
      </Card>

      <Dialog
        open={!!editTarget}
        onOpenChange={(open) => !open && setEditTarget(null)}
        trigger={<span />}
        title={`编辑 ${editTarget?.username ?? "账号"}`}
        description="用户名和账号类型保持不变；姓名用于界面展示。"
      >
        <form className="grid gap-4" onSubmit={updateDisplayName}>
          <Input
            label="姓名"
            name="display_name"
            defaultValue={editTarget?.display_name}
            maxLength={120}
            required
          />
          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setEditTarget(null)}
            >
              取消
            </Button>
            <Button type="submit" loading={busyId.startsWith("edit-")}>
              保存姓名
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={!!resetTarget}
        onOpenChange={(open) => !open && setResetTarget(null)}
        trigger={<span />}
        title={`重置 ${resetTarget?.display_name ?? "账号"} 的密码`}
        description="保存后该账号在所有设备上的会话都会立即退出。"
      >
        <form className="grid gap-4" onSubmit={resetPassword}>
          <Input
            label="新密码"
            name="password"
            type="password"
            minLength={8}
            required
          />
          <Input
            label="确认新密码"
            name="password_confirmation"
            type="password"
            minLength={8}
            required
          />
          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setResetTarget(null)}
            >
              取消
            </Button>
            <Button type="submit" loading={busyId.startsWith("reset-")}>
              重置并退出所有设备
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
