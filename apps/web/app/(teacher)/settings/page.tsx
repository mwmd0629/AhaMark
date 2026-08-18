"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuthUser } from "@/components/auth-gate";
import { HealthStatus } from "@/components/health-status";
import {
  Button,
  Card,
  ErrorState,
  Input,
  PageHeader,
  SectionHeader,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  ApiError,
  authApi,
  classesApi,
  type ClassRecord,
  type TeacherPreferences,
} from "@/lib/api";

const defaults: TeacherPreferences["preferences"] = {
  default_class_id: null,
  rubric_status_filter: "all",
  rubric_page_size: 20,
  compact_rubric_cards: false,
};

export default function SettingsPage() {
  const toast = useToast();
  const user = useAuthUser();
  const [settings, setSettings] = useState<TeacherPreferences>();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [preferences, setPreferences] =
    useState<TeacherPreferences["preferences"]>(defaults);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextSettings, classPage] = await Promise.all([
        authApi.preferences(),
        classesApi.list("page=1&page_size=100&status=active&sort=name_asc"),
      ]);
      setSettings(nextSettings);
      setDisplayName(nextSettings.profile.display_name);
      setPreferences(nextSettings.preferences);
      setClasses(classPage.items);
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "设置加载失败，请确认后端服务正常。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    if (!settings || !displayName.trim()) return;
    setSaving(true);
    try {
      const updated = await authApi.updatePreferences({
        expected_revision: settings.revision,
        display_name: displayName.trim(),
        preferences,
      });
      setSettings(updated);
      setDisplayName(updated.profile.display_name);
      setPreferences(updated.preferences);
      toast("设置已保存到当前账户");
    } catch (reason) {
      toast(
        reason instanceof ApiError ? reason.message : "设置保存失败，请重试。",
        "error",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="设置"
        description="管理当前教师账户资料和评分模板工作台偏好。服务器密钥与 AI 开关不在此页面开放。"
      />
      {loading ? (
        <div className="space-y-4" aria-label="正在加载设置">
          <Skeleton className="h-40" />
          <Skeleton className="h-52" />
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[220px_1fr]">
          <nav
            aria-label="设置分区"
            className="h-fit rounded-xl border border-[var(--border)] bg-white p-2"
          >
            {["账户资料", "模板工作台", "AI 与隐私", "系统连接状态"].map(
              (item, index) => (
                <a
                  key={item}
                  href={`#setting-${index}`}
                  className="block rounded-lg px-3 py-2.5 text-sm hover:bg-slate-50"
                >
                  {item}
                </a>
              ),
            )}
          </nav>
          <div className="space-y-6">
            <Card id="setting-0" className="p-5">
              <SectionHeader
                title="账户资料"
                description="姓名会保存到当前账户；登录邮箱由管理员维护。"
              />
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <Input
                  label="姓名"
                  required
                  maxLength={120}
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
                <Input
                  label="邮箱"
                  value={settings?.profile.email ?? user?.email ?? ""}
                  readOnly
                />
              </div>
            </Card>

            <Card id="setting-1" className="p-5">
              <SectionHeader
                title="评分模板工作台"
                description="这些偏好会在评分模板页首次打开时作为默认筛选使用。"
              />
              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <Select
                  label="默认班级"
                  value={preferences.default_class_id ?? ""}
                  onChange={(event) =>
                    setPreferences({
                      ...preferences,
                      default_class_id: event.target.value || null,
                    })
                  }
                >
                  <option value="">全部班级</option>
                  {classes.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </Select>
                <Select
                  label="默认模板状态"
                  value={preferences.rubric_status_filter}
                  onChange={(event) =>
                    setPreferences({
                      ...preferences,
                      rubric_status_filter: event.target.value as
                        "all" | "draft" | "confirmed" | "retired",
                    })
                  }
                >
                  <option value="all">全部状态</option>
                  <option value="draft">草稿</option>
                  <option value="confirmed">已确认</option>
                  <option value="retired">已停用</option>
                </Select>
                <Select
                  label="每页模板数"
                  value={String(preferences.rubric_page_size)}
                  onChange={(event) =>
                    setPreferences({
                      ...preferences,
                      rubric_page_size: Number(event.target.value) as
                        10 | 20 | 50,
                    })
                  }
                >
                  <option value="10">10 条</option>
                  <option value="20">20 条</option>
                  <option value="50">50 条</option>
                </Select>
                <label className="flex min-h-10 items-center gap-3 self-end rounded-lg border border-[var(--border)] px-3 text-sm">
                  <input
                    type="checkbox"
                    checked={preferences.compact_rubric_cards}
                    onChange={(event) =>
                      setPreferences({
                        ...preferences,
                        compact_rubric_cards: event.target.checked,
                      })
                    }
                  />
                  使用紧凑模板卡片
                </label>
              </div>
            </Card>

            <Card id="setting-2" className="p-5">
              <SectionHeader
                title="AI 与数据隐私"
                description="仅展示服务器托管状态，不提供密钥、模型或外部请求开关。"
              />
              <div className="mt-4 rounded-xl border border-[var(--border)] bg-slate-50 p-4 text-sm">
                <p className="font-semibold">
                  外部 AI 请求：
                  {settings?.server_managed.external_ai_enabled
                    ? "由服务器管理员启用"
                    : "已关闭"}
                </p>
                <p className="mt-2 leading-6 text-[var(--text-secondary)]">
                  API 密钥与安全配置只存在于服务端 Secret
                  管理中。教师设置页不能读取、修改或提交这些值；最终成绩仍由教师确认。
                </p>
              </div>
            </Card>

            <Card id="setting-3" className="p-5">
              <SectionHeader
                title="系统连接状态"
                description="来自当前后端的实时健康检查。"
              />
              <div className="mt-4">
                <HealthStatus />
              </div>
            </Card>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-[var(--text-secondary)]">
                {settings?.updated_at
                  ? `上次保存：${new Date(settings.updated_at).toLocaleString("zh-CN")}`
                  : "尚未保存过账户偏好"}
              </p>
              <Button
                loading={saving}
                disabled={!displayName.trim()}
                onClick={() => void save()}
              >
                保存设置
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
