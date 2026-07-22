import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: { default: "AhaMark", template: "%s · AhaMark" },
  description: "面向教师的 AI 作业批改与学情分析平台",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
