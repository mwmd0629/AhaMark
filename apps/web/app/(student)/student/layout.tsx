import { AuthGate } from "@/components/auth-gate";
import { StudentShell } from "@/components/student-shell";

export default function StudentLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGate audience="student">
      <StudentShell>{children}</StudentShell>
    </AuthGate>
  );
}
