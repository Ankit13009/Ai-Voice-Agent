import type { ReactNode } from "react";

import { cn } from "./utils";

/** Loading placeholder. Sized by the caller so it matches the content it replaces. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded bg-surface-sunken",
        "after:absolute after:inset-0 after:-translate-x-full after:animate-shimmer",
        "after:bg-gradient-to-r after:from-transparent after:via-black/5 after:to-transparent",
        className,
      )}
      aria-hidden="true"
    />
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6">
      {icon && <div className="mb-3 text-ink-subtle">{icon}</div>}
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      {description && (
        <p className="mt-1 text-sm text-ink-subtle max-w-sm">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export type AlertTone = "info" | "success" | "warning" | "danger";

const ALERT_TONES: Record<AlertTone, string> = {
  info: "bg-info-soft border-info/25 text-info",
  success: "bg-success-soft border-success/25 text-success",
  warning: "bg-warning-soft border-warning/25 text-warning",
  danger: "bg-danger-soft border-danger/25 text-danger",
};

/**
 * Inline banner for page-level state: a failed integration, a partial result.
 *
 * `role="alert"` only on the tones that represent a problem, so a routine
 * informational banner does not interrupt a screen reader mid-sentence.
 */
export function Alert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: AlertTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const isProblem = tone === "danger" || tone === "warning";
  return (
    <div
      role={isProblem ? "alert" : undefined}
      className={cn(
        "flex items-start justify-between gap-4 rounded-lg border px-4 py-3",
        ALERT_TONES[tone],
      )}
    >
      <div className="min-w-0 text-sm">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={cn(title && "mt-0.5", "opacity-90")}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/** Full-page loading state, used while a route resolves its first request. */
export function PageLoading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label={label}>
      <Skeleton className="h-8 w-56" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-24 w-full rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-72 w-full rounded-lg" />
    </div>
  );
}
