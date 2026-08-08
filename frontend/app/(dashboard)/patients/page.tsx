"use client";

import { useMemo, useState } from "react";

import { patientApi } from "@/lib/api/endpoints";
import { useApiList, useApiQuery } from "@/lib/useApi";
import { formatDate, formatPhone } from "@/lib/format";
import type { Patient } from "@/types/api";
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

export default function PatientsPage() {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useApiList<Patient>(
    (page, signal) =>
      patientApi.list({ page, page_size: 20, search: search.trim() || undefined }, signal),
    search,
  );

  const columns = useMemo<Array<Column<Patient>>>(
    () => [
      {
        key: "patient",
        header: "Patient",
        render: (patient) => (
          <CellStack
            primary={patient.name || "Unnamed"}
            secondary={formatPhone(patient.phone)}
          />
        ),
      },
      {
        key: "language",
        header: "Language",
        hideOnMobile: true,
        render: (patient) => <LanguageBadge language={patient.preferred_language} />,
      },
      {
        key: "created",
        header: "First seen",
        hideOnMobile: true,
        align: "right",
        render: (patient) => (
          <span className="text-sm text-ink-muted tnum">
            {formatDate(patient.created_at)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Patients"
        description="Everyone who has called or been booked in. Created automatically from calls."
      />

      <Card>
        <div className="px-5 py-3 border-b border-line">
          <Input
            className="max-w-xs"
            placeholder="Search by name or phone"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search patients"
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
          rowKey={(patient) => patient.id}
          loading={list.loading}
          onRowClick={(patient) => setSelectedId(patient.id)}
          empty={
            <EmptyState
              title="No patients yet"
              description={
                search
                  ? "No patients match that search."
                  : "A patient record is created the first time someone calls or is booked in."
              }
            />
          }
        />

        {list.meta && <Pagination meta={list.meta} onPageChange={list.setPage} />}
      </Card>

      <PatientDetailModal patientId={selectedId} onClose={() => setSelectedId(null)} />
    </>
  );
}

function PatientDetailModal({
  patientId,
  onClose,
}: {
  patientId: string | null;
  onClose: () => void;
}) {
  const detail = useApiQuery(
    (signal) => (patientId ? patientApi.get(patientId, signal) : Promise.resolve(null)),
    [patientId],
  );

  const patient = detail.data;

  return (
    <Modal
      open={Boolean(patientId)}
      onClose={onClose}
      title={patient?.name || "Patient"}
      description={patient ? formatPhone(patient.phone) : undefined}
      size="lg"
    >
      {detail.loading && <p className="text-sm text-ink-subtle">Loading…</p>}
      {detail.error && <Alert tone="danger">{detail.error.message}</Alert>}

      {patient && (
        <div className="flex flex-col gap-5">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-2xs uppercase tracking-wide text-ink-subtle">
                Total appointments
              </dt>
              <dd className="text-sm text-ink mt-0.5 tnum">
                {patient.total_appointments}
              </dd>
            </div>
            <div>
              <dt className="text-2xs uppercase tracking-wide text-ink-subtle">
                Upcoming
              </dt>
              <dd className="text-sm text-ink mt-0.5 tnum">
                {patient.upcoming_appointments}
              </dd>
            </div>
          </dl>

          {patient.notes && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-1.5">
                Notes
              </h3>
              <p className="text-sm text-ink-muted">{patient.notes}</p>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-2">
              Appointment history
            </h3>
            {patient.appointments.length === 0 ? (
              <p className="text-sm text-ink-subtle">No appointments yet.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-line border border-line rounded-lg overflow-hidden">
                {patient.appointments.map((appointment) => (
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
