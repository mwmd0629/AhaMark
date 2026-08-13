export function formatDateTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatScore(
  value: number | string | null | undefined,
  total: number | string | null | undefined,
) {
  if (value === null || value === undefined || value === "—") return "仅反馈";
  if (total === null || total === undefined) return String(value);
  return `${value} / ${total}`;
}

export function safeExternalUrl(value?: string | null) {
  if (!value) return null;
  if (value.startsWith("/")) return value;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}
