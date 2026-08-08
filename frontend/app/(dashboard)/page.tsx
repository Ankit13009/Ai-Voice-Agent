"use client";

import Link from "next/link";

import { businessApi, dashboardApi } from "@/lib/api/endpoints";
import { useApiQuery } from "@/lib/useApi";
import { formatDuration } from "@/lib/format";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  PageLoading,
  StatCard,
} from "@/components/ui";

export default function OverviewPage() {
  const stats = useApiQuery((signal) => dashboardApi.stats(signal), []);
  const business = useApiQuery((signal) => businessApi.me(signal), []);

  if (stats.loading || business.loading) return <PageLoading />;

  if (stats.error) {
    return (
      <Alert tone="danger" title="Could not load the dashboard">
        {stats.error.message}
      </Alert>
    );
  }

  const data = stats.data;
  const integrations = business.data?.integrations;

  // Surfaced at the top because each one silently breaks a core feature: no
  // calendar means the agent cannot book, no WhatsApp means no reminders.
  const problems: Array<{ label: string; href: string }> = [];
  if (integrations && !integrations.google_calendar_connected) {
    problems.push({
      label: "Google Calendar is not connected, so the agent cannot check or book slots.",
      href: "/settings",
    });
  }
  if (integrations && !integrations.whatsapp_configured) {
    problems.push({
      label: "WhatsApp is not configured, so confirmations and reminders will not send.",
      href: "/settings",
    });
  }
  if (integrations && !integrations.vapi_assistant_configured) {
    problems.push({
      label: "No voice assistant is attached, so inbound calls will not be answered.",
      href: "/settings",
    });
  }

  return (
    <>
      <PageHeader
        title="Overview"
        description={business.data ? `${business.data.name} · ${business.data.phone_number}` : undefined}
      />

      {problems.length > 0 && (
        <div className="flex flex-col gap-2 mb-6">
          {problems.map((problem) => (
            <Alert
              key={problem.label}
              tone="warning"
              action={
                <Link href={problem.href}>
                  <Button size="sm" variant="secondary">
                    Fix
                  </Button>
                </Link>
              }
            >
              {problem.label}
            </Alert>
          ))}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Calls handled"
          value={data?.calls_total ?? 0}
          hint={`${data?.calls_today ?? 0} today`}
        />
        <StatCard
          label="Booked by agent"
          value={data?.booked_by_agent ?? 0}
          hint={`${data?.conversion_rate ?? 0}% of calls`}
          tone="success"
        />
        <StatCard
          label="Upcoming appointments"
          value={data?.appointments_upcoming ?? 0}
          hint={`${data?.appointments_today ?? 0} today`}
        />
        <StatCard
          label="Average call length"
          value={formatDuration(data?.avg_call_duration_seconds ?? 0)}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2 mt-4">
        <Card>
          <CardHeader
            title="WhatsApp"
            description="Confirmations and reminders sent to customers."
            action={
              <Link href="/messages">
                <Button size="sm" variant="ghost">
                  View log
                </Button>
              </Link>
            }
          />
          <CardBody>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-2xs uppercase tracking-wide text-ink-subtle">
                  Delivered
                </p>
                <p className="text-xl font-semibold text-ink tnum mt-1">
                  {data?.whatsapp_sent ?? 0}
                </p>
              </div>
              <div>
                <p className="text-2xs uppercase tracking-wide text-ink-subtle">
                  Failed
                </p>
                <p
                  className={`text-xl font-semibold tnum mt-1 ${
                    (data?.whatsapp_failed ?? 0) > 0 ? "text-danger" : "text-ink"
                  }`}
                >
                  {data?.whatsapp_failed ?? 0}
                </p>
              </div>
            </div>
            {(data?.whatsapp_failed ?? 0) > 0 && (
              <p className="text-xs text-ink-subtle mt-3">
                Failed messages are usually an unapproved template or a number that is
                not on WhatsApp. Open the log to see the reason and retry.
              </p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Call outcomes"
            description="What happened on the calls the agent answered."
            action={
              <Link href="/calls">
                <Button size="sm" variant="ghost">
                  View calls
                </Button>
              </Link>
            }
          />
          <CardBody>
            <dl className="flex flex-col gap-2.5 text-sm">
              <Row label="Appointments booked" value={data?.booked_by_agent ?? 0} />
              <Row label="Cancelled" value={data?.cancelled ?? 0} />
              <Row
                label="Ended without details"
                value={data?.no_details ?? 0}
                hint="Caller hung up before giving a name or number."
              />
            </dl>
          </CardBody>
        </Card>
      </div>
    </>
  );
}

function Row({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <dt className="text-ink">{label}</dt>
        {hint && <p className="text-xs text-ink-subtle mt-0.5">{hint}</p>}
      </div>
      <dd className="text-ink font-medium tnum shrink-0">{value}</dd>
    </div>
  );
}
