import { describe, expect, it } from "vitest";
import { parseAccountCsv } from "./account-csv";

describe("account CSV preview", () => {
  it("supports English and Chinese values plus quoted commas", () => {
    const preview = parseAccountCsv(
      "username,display_name,account_type,password\r\n" +
        'Teacher-01,"王老师,数学",教师,secure-pass-123\r\n' +
        "student-01,张同学,student,secure-pass-456\r\n",
    );
    expect(preview.file_errors).toEqual([]);
    expect(preview.rows).toMatchObject([
      {
        row_number: 2,
        username: "teacher-01",
        display_name: "王老师,数学",
        account_type: "teacher",
        errors: [],
      },
      {
        row_number: 3,
        username: "student-01",
        account_type: "student",
        errors: [],
      },
    ]);
  });

  it("reports duplicate, invalid role and short password by row", () => {
    const preview = parseAccountCsv(
      "用户名,姓名,账号类型,初始密码\n" +
        "same-user,甲,教师,secure-pass-123\n" +
        "same-user,乙,管理员,short\n",
    );
    expect(preview.rows[1].errors).toEqual([
      "账号类型只能是教师/teacher 或学生/student",
      "初始密码须为 8–256 个字符",
      "CSV 中用户名重复",
    ]);
  });

  it("rejects missing headers and more than 200 data rows", () => {
    expect(
      parseAccountCsv("username,display_name\na,b").file_errors,
    ).toHaveLength(1);
    const rows = Array.from(
      { length: 201 },
      (_, index) => `student-${index},学生${index},student,secure-pass-123`,
    );
    expect(
      parseAccountCsv(
        `username,display_name,account_type,password\n${rows.join("\n")}`,
      ).file_errors,
    ).toEqual(["每次最多导入 200 个账号"]);
  });
});
