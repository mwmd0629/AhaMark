import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
export default function TeacherLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGate>
      <AppShell>{children}</AppShell>
    </AuthGate>
  );
}
