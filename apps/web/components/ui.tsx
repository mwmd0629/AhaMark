"use client";
import {
  createContext,
  forwardRef,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { Icon } from "./icons";

function cx(...values: (string | false | null | undefined)[]) {
  return values.filter(Boolean).join(" ");
}
const buttonStyles = {
  primary: "bg-[var(--brand-600)] text-white hover:bg-[var(--brand-700)]",
  secondary:
    "bg-[var(--brand-50)] text-[var(--brand-700)] hover:bg-[var(--brand-100)]",
  outline: "border border-[var(--border)] bg-white hover:bg-slate-50",
  ghost: "hover:bg-slate-100",
  danger: "bg-[var(--danger)] text-white hover:opacity-90",
};
export function Button({
  variant = "primary",
  loading = false,
  className,
  disabled,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof buttonStyles;
  loading?: boolean;
}) {
  return (
    <button
      className={cx(
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-[var(--radius-md)] px-4 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
        buttonStyles[variant],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent" />
      )}
      {children}
    </button>
  );
}
export function Input({
  label,
  description,
  error,
  required,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  description?: string;
  error?: string;
}) {
  const id = useId();
  return (
    <label className="grid gap-1.5 text-sm font-medium" htmlFor={id}>
      {label && (
        <span>
          {label}
          {required && <span className="text-[var(--danger)]"> *</span>}
        </span>
      )}
      <input
        id={id}
        required={required}
        aria-invalid={!!error}
        aria-describedby={description || error ? `${id}-help` : undefined}
        className={cx(
          "h-10 rounded-[var(--radius-md)] border bg-white px-3 font-normal outline-none transition placeholder:text-slate-400 focus:border-[var(--brand-500)] disabled:bg-slate-100",
          error ? "border-[var(--danger)]" : "border-[var(--border)]",
          className,
        )}
        {...props}
      />
      {(description || error) && (
        <span
          id={`${id}-help`}
          className={cx(
            "text-xs font-normal",
            error ? "text-[var(--danger)]" : "text-[var(--text-secondary)]",
          )}
        >
          {error || description}
        </span>
      )}
    </label>
  );
}
export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { label?: string }
>(function Select({ label, children, className, ...props }, ref) {
  const id = useId();
  return (
    <label className="grid gap-1.5 text-sm font-medium" htmlFor={id}>
      {label && <span>{label}</span>}
      <select
        ref={ref}
        id={id}
        className={cx(
          "h-10 rounded-[var(--radius-md)] border border-[var(--border)] bg-white px-3 font-normal",
          className,
        )}
        {...props}
      >
        {children}
      </select>
    </label>
  );
});
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx(
        "rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-card)] shadow-[var(--shadow-sm)]",
        className,
      )}
      {...props}
    />
  );
}
const badgeMap = {
  draft: ["草稿", "bg-slate-100 text-slate-700"],
  processing: ["AI 批改中", "bg-purple-50 text-purple-700"],
  "pending-review": ["待复核", "bg-amber-50 text-amber-800"],
  completed: ["已完成", "bg-emerald-50 text-emerald-700"],
  published: ["已发布", "bg-blue-50 text-blue-700"],
  grading: ["批改中", "bg-purple-50 text-purple-700"],
  failed: ["异常", "bg-red-50 text-red-700"],
  queued: ["排队中", "bg-slate-100 text-slate-700"],
  running: ["识别中", "bg-purple-50 text-purple-700"],
  partially_completed: ["部分完成", "bg-amber-50 text-amber-800"],
  cancelled: ["已取消", "bg-slate-100 text-slate-600"],
  stale: ["待重新识别", "bg-amber-50 text-amber-800"],
  archived: ["已归档", "bg-slate-100 text-slate-600"],
} as const;
export function Badge({ status }: { status: keyof typeof badgeMap }) {
  const [label, style] = badgeMap[status];
  return (
    <span
      className={cx(
        "inline-flex rounded-full px-2.5 py-1 text-xs font-semibold",
        style,
      )}
    >
      {label}
    </span>
  );
}
export function DemoBadge() {
  return (
    <span className="inline-flex rounded-full border border-dashed border-[var(--brand-500)] px-2 py-0.5 text-[11px] font-semibold text-[var(--brand-700)]">
      演示数据
    </span>
  );
}
export function Progress({ value, label }: { value: number; label?: string }) {
  return (
    <div className="grid gap-1.5">
      <div className="flex justify-between text-xs text-[var(--text-secondary)]">
        <span>{label}</span>
        <span>{value}%</span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
      >
        <div
          className="h-full rounded-full bg-[var(--brand-500)]"
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
}
export function Table({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table
        className={cx(
          "w-full min-w-[680px] border-collapse text-left text-sm",
          className,
        )}
      >
        {children}
      </table>
    </div>
  );
}
export function Avatar({
  initials,
  size = "md",
}: {
  initials: string;
  size?: "sm" | "md";
}) {
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--brand-100)] font-bold text-[var(--brand-700)]",
        size === "sm" ? "h-8 w-8 text-xs" : "h-10 w-10 text-sm",
      )}
    >
      {initials}
    </span>
  );
}
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div>
        {eyebrow}
        <h1 className="text-2xl font-bold tracking-tight sm:text-[1.75rem]">
          {title}
        </h1>
        <p className="mt-1.5 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
          {description}
        </p>
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>
      )}
    </header>
  );
}
export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div>
        <h2 className="text-base font-bold">{title}</h2>
        {description && (
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}
export function StatCard({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <Card className="p-5">
      <p className="text-sm text-[var(--text-secondary)]">{label}</p>
      <p className="mt-2 text-3xl font-bold tracking-tight">{value}</p>
      <p className="mt-1 text-xs text-[var(--text-secondary)]">{note}</p>
    </Card>
  );
}
export function EmptyState({
  title,
  description,
  action,
  icon = "assignments",
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: string;
}) {
  return (
    <div className="grid min-h-52 place-items-center rounded-[var(--radius-lg)] border border-dashed border-[var(--border)] bg-white p-8 text-center">
      <div>
        <span className="mx-auto mb-4 grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-500">
          <Icon name={icon} />
        </span>
        <h3 className="font-bold">{title}</h3>
        <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[var(--text-secondary)]">
          {description}
        </p>
        {action && <div className="mt-5">{action}</div>}
      </div>
    </div>
  );
}
export function ErrorState({
  title = "暂时无法加载",
  description,
  retry,
}: {
  title?: string;
  description: string;
  retry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-[var(--radius-lg)] border border-red-200 bg-red-50 p-5"
    >
      <h3 className="font-bold text-red-800">{title}</h3>
      <p className="mt-1 text-sm text-red-700">{description}</p>
      {retry && (
        <Button variant="outline" className="mt-4" onClick={retry}>
          重试
        </Button>
      )}
    </div>
  );
}
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx("animate-pulse rounded-md bg-slate-200", className)}
    />
  );
}
export function ConfidenceIndicator({ value }: { value: number }) {
  const label =
    value >= 0.85 ? "高置信度" : value >= 0.6 ? "建议确认" : "需人工复核";
  const color =
    value >= 0.85
      ? "var(--success)"
      : value >= 0.6
        ? "var(--warning)"
        : "var(--danger)";
  return (
    <div
      className="flex items-center gap-2 text-sm"
      aria-label={`${label} ${Math.round(value * 100)}%`}
    >
      <span
        className="h-2.5 w-2.5 rounded-full"
        style={{ background: color }}
      />
      <span>{label}</span>
      <strong>{Math.round(value * 100)}%</strong>
    </div>
  );
}
export function Breadcrumb({ items }: { items: string[] }) {
  return (
    <nav
      aria-label="面包屑"
      className="flex items-center gap-1.5 text-sm text-[var(--text-secondary)]"
    >
      <span>教师端</span>
      {items.map((item) => (
        <span className="flex items-center gap-1.5" key={item}>
          <Icon name="chevron" className="h-3.5 w-3.5" />
          <span aria-current="page">{item}</span>
        </span>
      ))}
    </nav>
  );
}

export function Dialog({
  trigger,
  title,
  description,
  children,
  dismissible = true,
  open: controlledOpen,
  onOpenChange,
}: {
  trigger: ReactNode;
  title: string;
  description?: string;
  children: ReactNode;
  dismissible?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const changeOpen = (nextOpen: boolean) => {
    if (controlledOpen === undefined) setInternalOpen(nextOpen);
    onOpenChange?.(nextOpen);
  };
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (dismissible && e.key === "Escape") {
        if (controlledOpen === undefined) setInternalOpen(false);
        onOpenChange?.(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [controlledOpen, dismissible, onOpenChange, open]);
  return (
    <>
      {<span onClick={() => changeOpen(true)}>{trigger}</span>}
      {open && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4"
          role="presentation"
          onMouseDown={() => dismissible && changeOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="dialog-title"
            className="w-full max-w-lg rounded-[var(--radius-lg)] bg-white p-6 shadow-2xl"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between gap-4">
              <div>
                <h2 id="dialog-title" className="text-lg font-bold">
                  {title}
                </h2>
                {description && (
                  <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    {description}
                  </p>
                )}
              </div>
              <button
                ref={closeRef}
                aria-label="关闭对话框"
                onClick={() => changeOpen(false)}
                className="grid h-9 w-9 place-items-center rounded-lg hover:bg-slate-100"
              >
                <Icon name="close" />
              </button>
            </div>
            <div className="mt-5">{children}</div>
          </div>
        </div>
      )}
    </>
  );
}
export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-slate-950/40" onMouseDown={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className="h-full w-[min(88vw,320px)] bg-white p-4 shadow-2xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <strong>{title}</strong>
          <button aria-label="关闭抽屉" onClick={onClose} className="p-2">
            <Icon name="close" />
          </button>
        </div>
        {children}
      </aside>
    </div>
  );
}
export function Dropdown({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        rootRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
  return (
    <div ref={rootRef} className="relative">
      <button
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="rounded-lg"
      >
        {label}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-2 min-w-48 rounded-xl border border-[var(--border)] bg-white p-2 shadow-[var(--shadow-md)]"
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  );
}
type ToastItem = { id: number; message: string; tone: "success" | "error" };
const ToastContext = createContext<
  (message: string, tone?: ToastItem["tone"]) => void
>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const show = (message: string, tone: ToastItem["tone"] = "success") => {
    const id = Date.now();
    setItems((old) => [...old, { id, message, tone }]);
    window.setTimeout(
      () => setItems((old) => old.filter((item) => item.id !== id)),
      3200,
    );
  };
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div
        aria-live="polite"
        className="fixed bottom-5 right-5 z-[60] grid gap-2"
      >
        {items.map((item) => (
          <div
            role="status"
            key={item.id}
            className={cx(
              "rounded-xl px-4 py-3 text-sm font-semibold text-white shadow-lg",
              item.tone === "success" ? "bg-emerald-700" : "bg-red-700",
            )}
          >
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);
