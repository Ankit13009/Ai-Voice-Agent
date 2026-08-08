import type { ReactNode } from "react";

import { cn } from "./utils";

/**
 * Surface primitives.
 *
 * `Card` is the only container that draws a border and background. Pages
 * compose these rather than styling `div`s, which is what keeps padding and
 * corner radius identical across every screen.
 */

export function Card({
  className,
  children,
  as: Component = "div",
}: {
  className?: string;
  children: ReactNode;
  as?: "div" | "section" | "article";
}) {
  return (
    <Component
      className={cn(
        "bg-surface border border-line rounded-lg shadow-xs overflow-hidden",
        className,
      )}
    >
      {children}
    </Component>
  );
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Right-aligned control, typically a Button. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 px-5 py-4 border-b border-line",
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description && (
          <p className="text-xs text-ink-subtle mt-0.5">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function CardBody({
  className,
  children,
  /** Turn off when the body holds a table, which brings its own edge padding. */
  padded = true,
}: {
  className?: string;
  children: ReactNode;
  padded?: boolean;
}) {
  return <div className={cn(padded && "p-5", className)}>{children}</div>;
}

export function CardFooter({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-2 px-5 py-3 border-t border-line bg-surface-sunken",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * A single headline number.
 *
 * The value uses tabular numerals so a row of these stays optically aligned as
 * the numbers change, rather than jittering on every refresh.
 */
export function StatCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "success" | "warning" | "danger";
}) {
  const toneClass = {
    default: "text-ink",
    success: "text-success",
    warning: "text-warning",
    danger: "text-danger",
  }[tone];

  return (
    <Card className="p-5">
      <p className="text-2xs font-medium uppercase tracking-wide text-ink-subtle">
        {label}
      </p>
      <p className={cn("mt-2 text-2xl font-semibold tnum", toneClass)}>{value}</p>
      {hint && <p className="mt-1 text-xs text-ink-subtle">{hint}</p>}
    </Card>
  );
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {description && (
          <p className="text-sm text-ink-muted mt-1">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
