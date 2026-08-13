import type { AuthUser } from "@/lib/api";

export function landingPath(
  surface: AuthUser["landing_surface"],
): string | null {
  if (surface === "teacher") return "/dashboard";
  if (surface === "student") return "/student";
  if (surface === "change_password") return "/change-password";
  return null;
}

export const accountUnavailableMessage =
  "当前账号暂时无法进入系统，请联系教师或管理员检查学生账号绑定状态。";
