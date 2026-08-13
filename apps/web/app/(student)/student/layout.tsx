import { StudentAuthGate } from "@/components/student-auth-gate";
import { StudentShell } from "@/components/student-shell";

export default function StudentLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <StudentAuthGate>
      <StudentShell>{children}</StudentShell>
    </StudentAuthGate>
  );
}
