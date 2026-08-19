import type { AccountType } from "@/lib/api";

export type CsvAccountRow = {
  row_number: number;
  username: string;
  display_name: string;
  password: string;
  account_type: Extract<AccountType, "teacher" | "student"> | "";
  errors: string[];
};

export type CsvAccountPreview = {
  rows: CsvAccountRow[];
  file_errors: string[];
};

export const ACCOUNT_CSV_TEMPLATE =
  "username,display_name,account_type,password\r\n" +
  "teacher01,王老师,teacher,replace-with-initial-password\r\n" +
  "student01,张同学,student,replace-with-initial-password\r\n";

function parseRecords(source: string): string[][] {
  const records: string[][] = [];
  let record: string[] = [];
  let field = "";
  let quoted = false;
  const text = source.replace(/^\uFEFF/, "");
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        field += character;
      }
      continue;
    }
    if (character === '"' && field === "") {
      quoted = true;
    } else if (character === ",") {
      record.push(field);
      field = "";
    } else if (character === "\n" || character === "\r") {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      record.push(field);
      if (record.some((value) => value.trim())) records.push(record);
      record = [];
      field = "";
    } else {
      field += character;
    }
  }
  record.push(field);
  if (record.some((value) => value.trim())) records.push(record);
  return records;
}

const headerAliases: Record<string, string> = {
  username: "username",
  用户名: "username",
  display_name: "display_name",
  姓名: "display_name",
  account_type: "account_type",
  账号类型: "account_type",
  password: "password",
  初始密码: "password",
};

function normalizedType(
  value: string,
): Extract<AccountType, "teacher" | "student"> | "" {
  const normalized = value.trim().toLowerCase();
  if (normalized === "teacher" || normalized === "教师") return "teacher";
  if (normalized === "student" || normalized === "学生") return "student";
  return "";
}

export function parseAccountCsv(source: string): CsvAccountPreview {
  const records = parseRecords(source);
  if (!records.length) return { rows: [], file_errors: ["CSV 文件为空"] };
  const headers = records[0].map(
    (header) => headerAliases[header.trim().toLowerCase()] ?? "",
  );
  const required = ["username", "display_name", "account_type", "password"];
  const missing = required.filter((header) => !headers.includes(header));
  if (missing.length) {
    return {
      rows: [],
      file_errors: [
        "表头必须包含 username、display_name、account_type、password（也支持对应中文表头）",
      ],
    };
  }
  if (records.length - 1 > 200) {
    return { rows: [], file_errors: ["每次最多导入 200 个账号"] };
  }
  const seen = new Set<string>();
  const rows = records.slice(1).map((record, index): CsvAccountRow => {
    const values = Object.fromEntries(
      headers.map((header, column) => [header, record[column]?.trim() ?? ""]),
    );
    const username = (values.username ?? "").normalize("NFKC").toLowerCase();
    const displayName = values.display_name ?? "";
    const password = values.password ?? "";
    const accountType = normalizedType(values.account_type ?? "");
    const errors: string[] = [];
    if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(username))
      errors.push("用户名格式不正确");
    if (!displayName || displayName.length > 120)
      errors.push("姓名不能为空且最多 120 字");
    if (!accountType) errors.push("账号类型只能是教师/teacher 或学生/student");
    if (password.length < 8 || password.length > 256)
      errors.push("初始密码须为 8–256 个字符");
    if (seen.has(username)) errors.push("CSV 中用户名重复");
    if (username) seen.add(username);
    return {
      row_number: index + 2,
      username,
      display_name: displayName,
      password,
      account_type: accountType,
      errors,
    };
  });
  return { rows, file_errors: [] };
}
