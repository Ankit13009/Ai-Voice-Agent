"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { adminApi } from "@/lib/api/endpoints";
import { useApiQuery } from "@/lib/useApi";
import { formatDate, formatPaise, formatPhone } from "@/lib/format";
import type { AdminBusinessRow } from "@/types/api";
import { TenantUsersModal } from "@/components/admin/TenantUsersModal";
import {
  Alert,
  Badge,
  Button,
  Card,
  CellStack,
  EmptyState,
  PageHeader,
  PageLoading,
  StatCard,
  Table,
  type Column,
} from "@/components/ui";

/** Clients list: who exists, and what still needs finishing for each. */
export default function AdminClientsPage() {
  const businesses = useApiQuery((signal) => adminApi.listBusinesses(signal), []);
  const stats = useApiQuery((signal) => adminApi.stats(signal), []);
  const [usersFor, setUsersFor] = useState<AdminBusinessRow | null>(null);

  const columns = useMemo<Array<Column<AdminBusinessRow>>>(
    () => [
      {
        key: "name",
        header: "Client",
        render: (row) => (
          <CellStack
            primary={row.name}
            secondary={`${row.business_type} · ${row.city || "no city"}`}
          />
        ),
      },
      {
        key: "number",
        header: "Number",
        hideOnMobile: true,
        render: (row) => (
          <span className="text-sm text-ink-muted tnum">
            {formatPhone(row.phone_number)}
          </span>
        ),
      },
      {
        key: "owner",
        header: "Owner login",
        hideOnMobile: true,
        render: (row) => (
          <span className="text-sm text-ink-muted truncate">{row.owner_email || "—"}</span>
        ),
      },
      {
        key: "activity",
        header: "Calls (7d)",
        align: "right",
        hideOnMobile: true,
        render: (row) => (
          <span className="text-sm text-ink tnum">
            {row.calls_last_7d}
            <span className="text-ink-subtle"> / {row.calls_total}</span>
          </span>
        ),
      },
      {
        key: "setup",
        header: "Setup",
        render: (row) => {
          // Three things must all be true before a client can take a real call.
          // Showing them individually makes the missing one obvious at a glance.
          const items: Array<[string, boolean]> = [
            ["Voice", row.setup.voice_agent],
            ["Calendar", row.setup.google_calendar],
            ["WhatsApp", row.setup.whatsapp],
          ];
          const done = items.filter(([, ok]) => ok).length;
          return (
            <div className="flex items-center gap-1.5 flex-wrap">
              {done === items.length ? (
                <Badge tone="success" dot>
                  Live
                </Badge>
              ) : (
                items.map(([label, ok]) => (
                  <Badge key={label} tone={ok ? "success" : "warning"}>
                    {ok ? "✓" : "○"} {label}
                  </Badge>
                ))
              )}
            </div>
          );
        },
      },
      {
        key: "created",
        header: "Added",
        align: "right",
        hideOnMobile: true,
        render: (row) => (
          <span className="text-sm text-ink-subtle tnum">{formatDate(row.created_at)}</span>
        ),
      },
      {
        key: "actions",
        header: "",
        align: "right",
        render: (row) => (
          <Button size="sm" variant="ghost" onClick={() => setUsersFor(row)}>
            Users
          </Button>
        ),
      },
    ],
    [],
  );

  if (businesses.loading) return <PageLoading />;

  if (businesses.error) {
    return (
      <Alert tone="danger" title="Could not load clients">
        {businesses.error.message}
      </Alert>
    );
  }

  const rows = businesses.data ?? [];
  const platform = stats.data;

  return (
    <>
      <PageHeader
        title="Clients"
        description="Every business on the platform, and what each still needs."
        action={
          <Link href="/admin/onboard">
            <Button variant="primary">Add client</Button>
          </Link>
        }
      />

      {platform && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 mb-6">
          <StatCard
            label="Clients"
            value={platform.businesses_total}
            hint={`${platform.businesses_live} with a voice agent`}
          />
          <StatCard label="Calls handled" value={platform.calls_total} />
          <StatCard label="Appointments" value={platform.appointments_total} />
          {/* Usage cost is here because VAPI bills per minute: a heavy client on
              a flat plan can quietly be unprofitable. */}
          <StatCard
            label="Call spend"
            value={formatPaise(platform.call_cost_paise)}
            hint="Voice usage, all clients"
          />
        </div>
      )}

      <Card>
        <Table
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id}
          empty={
            <EmptyState
              title="No clients yet"
              description="Add your first business and its agent is provisioned automatically."
              action={
                <Link href="/admin/onboard">
                  <Button variant="primary">Add client</Button>
                </Link>
              }
            />
          }
        />
      </Card>

      <TenantUsersModal
        businessId={usersFor?.id ?? null}
        businessName={usersFor?.name ?? ""}
        onClose={() => setUsersFor(null)}
      />
    </>
  );
}
