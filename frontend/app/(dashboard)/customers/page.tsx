"use client";

import { useMemo, useState } from "react";

import { customerApi } from "@/lib/api/endpoints";
import { useApiList, useApiQuery } from "@/lib/useApi";
import { useLabels } from "@/lib/labels";
import { formatDate, formatPhone } from "@/lib/format";
import type { Customer } from "@/types/api";
import {
  Alert,
  AppointmentStatusBadge,
  Card,
  CellStack,
  EmptyState,
  Input,
  LanguageBadge,
  Modal,
  PageHeader,
  Pagination,
  Table,
  type Column,
} from "@/components/ui";

export default function CustomersPage() {
  const { title, lower } = useLabels();
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useApiList<Customer>(
    (page, signal) =>
      customerApi.list({ page, page_size: 20, search: search.trim() || undefined }, signal),
    search,
  );

  const columns = useMemo<Array<Column<Customer>>>(
    () => [
      {
        key: "customer",
        header: title("customer_singular"),
        render: (customer) => (
          <CellStack
            primary={customer.name || "Unnamed"}
            secondary={formatPhone(customer.phone)}
          />
        ),
      },
      {
        key: "language",
        header: "Language",
        hideOnMobile: true,
        render: (customer) => <LanguageBadge language={customer.preferred_language} />,
      },
      {
        key: "created",
        header: "First seen",
        hideOnMobile: true,
        align: "right",
        render: (customer) => (
          <span className="text-sm text-ink-muted tnum">
            {formatDate(customer.created_at)}
          </span>
        ),
      },
    ],
    [title],
  );

  return (
    <>
      <PageHeader
        title={title("customer_plural")}
        description={`Everyone who has called or been booked in. A ${lower("customer_singular")} record is created automatically from the first call.`}
      />

      <Card>
        <div className="px-5 py-3 border-b border-line">
          <Input
            className="max-w-xs"
            placeholder="Search by name or phone"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search customers"
          />
        </div>

        {list.error && (
          <div className="p-5">
            <Alert tone="danger">{list.error.message}</Alert>
          </div>
        )}

        <Table
          columns={columns}
          rows={list.items}
          rowKey={(customer) => customer.id}
          loading={list.loading}
          onRowClick={(customer) => setSelectedId(customer.id)}
          empty={
            <EmptyState
              title={`No ${lower("customer_plural")} yet`}
              description={
                search
                  ? "No customers match that search."
                  : "A customer record is created the first time someone calls or is booked in."
              }
            />
          }
        />

        {list.meta && <Pagination meta={list.meta} onPageChange={list.setPage} />}
      </Card>

      <CustomerDetailModal customerId={selectedId} onClose={() => setSelectedId(null)} />
    </>
  );
}

function CustomerDetailModal({
  customerId,
  onClose,
}: {
  customerId: string | null;
  onClose: () => void;
}) {
  const detail = useApiQuery(
    (signal) => (customerId ? customerApi.get(customerId, signal) : Promise.resolve(null)),
    [customerId],
  );

  const customer = detail.data;

  return (
    <Modal
      open={Boolean(customerId)}
      onClose={onClose}
      title={customer?.name || "Customer"}
      description={customer ? formatPhone(customer.phone) : undefined}
      size="lg"
    >
      {detail.loading && <p className="text-sm text-ink-subtle">Loading…</p>}
      {detail.error && <Alert tone="danger">{detail.error.message}</Alert>}

      {customer && (
        <div className="flex flex-col gap-5">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-2xs uppercase tracking-wide text-ink-subtle">
                Total appointments
              </dt>
              <dd className="text-sm text-ink mt-0.5 tnum">
                {customer.total_appointments}
              </dd>
            </div>
            <div>
              <dt className="text-2xs uppercase tracking-wide text-ink-subtle">
                Upcoming
              </dt>
              <dd className="text-sm text-ink mt-0.5 tnum">
                {customer.upcoming_appointments}
              </dd>
            </div>
          </dl>

          {customer.notes && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-1.5">
                Notes
              </h3>
              <p className="text-sm text-ink-muted">{customer.notes}</p>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-2">
              Appointment history
            </h3>
            {customer.appointments.length === 0 ? (
              <p className="text-sm text-ink-subtle">No appointments yet.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-line border border-line rounded-lg overflow-hidden">
                {customer.appointments.map((appointment) => (
                  <li
                    key={appointment.id}
                    className="flex items-center justify-between gap-3 px-4 py-2.5"
                  >
                    <div className="min-w-0">
                      <p className="text-sm text-ink tnum">
                        {formatDate(appointment.starts_at)}
                      </p>
                      {appointment.reason && (
                        <p className="text-xs text-ink-subtle truncate mt-0.5">
                          {appointment.reason}
                        </p>
                      )}
                    </div>
                    <AppointmentStatusBadge status={appointment.status} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </Modal>
  );
}
