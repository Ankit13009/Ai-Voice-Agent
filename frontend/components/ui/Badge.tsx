import type { ReactNode } from "react";

import type {
  AppointmentStatus,
  CallOutcome,
  Language,
  MessageStatus,
} from "@/types/api";
import { cn, type VariantMap } from "./utils";

export type BadgeTone =
  | "neutral"
  | "primary"
  | "success"
  | "warning"
  | "danger"
  | "info";

const TONES: VariantMap<BadgeTone> = {
  neutral: "bg-surface-sunken text-ink-muted border-line",
  primary: "bg-primary-soft text-primary border-primary/25",
  success: "bg-success-soft text-success border-success/25",
  warning: "bg-warning-soft text-warning border-warning/25",
  danger: "bg-danger-soft text-danger border-danger/25",
  info: "bg-info-soft text-info border-info/25",
};

export function Badge({
  tone = "neutral",
  children,
  className,
  dot = false,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  /** Adds a leading dot. Useful when the same tone repeats down a column. */
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
        "text-2xs font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/**
 * Domain status -> visual tone, mapped in one place.
 *
 * Centralising this is what makes "cancelled" the same red on the appointments
 * table, the call log, and the customer detail page. A component that needs a
 * status chip uses these rather than choosing a tone itself.
 */

const APPOINTMENT_STATUS: Record<
  AppointmentStatus,
  { tone: BadgeTone; label: string }
> = {
  scheduled: { tone: "success", label: "Scheduled" },
  rescheduled: { tone: "info", label: "Rescheduled" },
  cancelled: { tone: "danger", label: "Cancelled" },
  completed: { tone: "neutral", label: "Completed" },
  no_show: { tone: "warning", label: "No show" },
};

export function AppointmentStatusBadge({ status }: { status: AppointmentStatus }) {
  const config = APPOINTMENT_STATUS[status] ?? {
    tone: "neutral" as BadgeTone,
    label: status,
  };
  return (
    <Badge tone={config.tone} dot>
      {config.label}
    </Badge>
  );
}

const CALL_OUTCOME: Record<CallOutcome, { tone: BadgeTone; label: string }> = {
  booked: { tone: "success", label: "Booked" },
  rescheduled: { tone: "info", label: "Rescheduled" },
  cancelled: { tone: "warning", label: "Cancelled" },
  enquiry: { tone: "neutral", label: "Enquiry" },
  no_details: { tone: "neutral", label: "No details" },
  failed: { tone: "danger", label: "Failed" },
};

export function CallOutcomeBadge({ outcome }: { outcome: CallOutcome }) {
  const config = CALL_OUTCOME[outcome] ?? {
    tone: "neutral" as BadgeTone,
    label: outcome,
  };
  return (
    <Badge tone={config.tone} dot>
      {config.label}
    </Badge>
  );
}

const MESSAGE_STATUS: Record<MessageStatus, { tone: BadgeTone; label: string }> = {
  pending: { tone: "neutral", label: "Queued" },
  sent: { tone: "info", label: "Sent" },
  delivered: { tone: "success", label: "Delivered" },
  read: { tone: "success", label: "Read" },
  failed: { tone: "danger", label: "Failed" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};

export function MessageStatusBadge({ status }: { status: MessageStatus }) {
  const config = MESSAGE_STATUS[status] ?? {
    tone: "neutral" as BadgeTone,
    label: status,
  };
  return (
    <Badge tone={config.tone} dot>
      {config.label}
    </Badge>
  );
}

const LANGUAGE_LABEL: Record<Language, string> = {
  hi: "हिन्दी",
  en: "English",
  "hi-en": "Hindi + English",
};

export function LanguageBadge({ language }: { language: Language }) {
  return <Badge tone="neutral">{LANGUAGE_LABEL[language] ?? language}</Badge>;
}
