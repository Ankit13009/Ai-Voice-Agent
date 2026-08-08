"use client";

import { useMemo, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { appointmentApi, clinicApi } from "@/lib/api/endpoints";
import { useApiList, useApiQuery, useMutation } from "@/lib/useApi";
import { formatPhone, isoToLocalInput, localInputToIso } from "@/lib/format";
import type { Appointment, AppointmentStatus, Slot } from "@/types/api";
import {
  Alert,
  AppointmentStatusBadge,
  Button,
  Card,
  CellStack,
  ConfirmDialog,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Table,
  Textarea,
  useToast,
  type Column,
} from "@/components/ui";

const STATUS_OPTIONS = [
  { value: "scheduled", label: "Scheduled" },
  { value: "rescheduled", label: "Rescheduled" },
  { value: "cancelled", label: "Cancelled" },
  { value: "completed", label: "Completed" },
  { value: "no_show", label: "No show" },
];

export default function AppointmentsPage() {
  const toast = useToast();
  const [status, setStatus] = useState<AppointmentStatus | "">("");
  const [search, setSearch] = useState("");
  const [bookOpen, setBookOpen] = useState(false);
  const [rescheduling, setRescheduling] = useState<Appointment | null>(null);
  const [cancelling, setCancelling] = useState<Appointment | null>(null);

  const list = useApiList<Appointment>(
    (page, signal) =>
      appointmentApi.list(
        {
          page,
          page_size: 20,
          status: status || undefined,
          search: search.trim() || undefined,
        },
        signal,
      ),
    `${status}|${search}`,
  );

  const cancel = useMutation(async (appointment: Appointment, reason: string) => {
    const { message } = await appointmentApi.cancel(appointment.id, {
      reason,
      notify_patient: true,
    });
    toast.showSuccess(message, "Appointment cancelled.");
    setCancelling(null);
    list.refetch();
  });

  const columns = useMemo<Array<Column<Appointment>>>(
    () => [
      {
        key: "patient",
        header: "Patient",
        render: (appointment) => (
          <CellStack
            primary={appointment.patient.name || "Unnamed"}
            secondary={formatPhone(appointment.patient.phone)}
          />
        ),
      },
      {
        key: "when",
        header: "When",
        render: (appointment) => (
          // The API pre-renders this in the clinic's timezone, which is not
          // necessarily the timezone of the staff member reading the screen.
          <span className="text-sm text-ink tnum">{appointment.starts_at_local}</span>
        ),
      },
      {
        key: "doctor",
        header: "Doctor",
        hideOnMobile: true,
        render: (appointment) => (
          <span className="text-sm text-ink-muted">
            {appointment.doctor_name || "Any"}
          </span>
        ),
      },
      {
        key: "reason",
        header: "Reason",
        hideOnMobile: true,
        render: (appointment) => (
          <span className="text-sm text-ink-muted line-clamp-1">
            {appointment.reason || "—"}
          </span>
        ),
      },
      {
        key: "status",
        header: "Status",
        render: (appointment) => (
          <div className="flex items-center gap-2">
            <AppointmentStatusBadge status={appointment.status} />
            {!appointment.synced_to_calendar && (
              <span
                className="text-2xs text-warning"
                title="This appointment is not on the Google Calendar."
              >
                not synced
              </span>
            )}
          </div>
        ),
      },
      {
        key: "actions",
        header: "",
        align: "right",
        render: (appointment) => {
          const isActive =
            appointment.status === "scheduled" || appointment.status === "rescheduled";
          if (!isActive) return null;
          return (
            <div className="flex items-center justify-end gap-1">
              <Button size="sm" variant="ghost" onClick={() => setRescheduling(appointment)}>
                Move
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setCancelling(appointment)}>
                Cancel
              </Button>
            </div>
          );
        },
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Appointments"
        description="Everything booked by the agent or by your front desk."
        action={
          <Button variant="primary" onClick={() => setBookOpen(true)}>
            Book appointment
          </Button>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 px-5 py-3 border-b border-line">
          <Input
            className="max-w-xs"
            placeholder="Search by patient name or phone"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search appointments"
          />
          <Select
            className="max-w-[12rem]"
            options={STATUS_OPTIONS}
            placeholder="All statuses"
            value={status}
            onChange={(event) => setStatus(event.target.value as AppointmentStatus | "")}
            aria-label="Filter by status"
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
          rowKey={(appointment) => appointment.id}
          loading={list.loading}
          empty={
            <EmptyState
              title="No appointments"
              description={
                search || status
                  ? "No appointments match these filters."
                  : "Book one here, or wait for the agent to take its first call."
              }
              action={
                <Button variant="primary" onClick={() => setBookOpen(true)}>
                  Book appointment
                </Button>
              }
            />
          }
        />

        {list.meta && <Pagination meta={list.meta} onPageChange={list.setPage} />}
      </Card>

      <BookAppointmentModal
        open={bookOpen}
        onClose={() => setBookOpen(false)}
        onBooked={() => {
          setBookOpen(false);
          list.refetch();
        }}
      />

      <RescheduleModal
        appointment={rescheduling}
        onClose={() => setRescheduling(null)}
        onDone={() => {
          setRescheduling(null);
          list.refetch();
        }}
      />

      <ConfirmDialog
        open={Boolean(cancelling)}
        onClose={() => setCancelling(null)}
        onConfirm={() => cancelling && cancel.run(cancelling, "Cancelled from the dashboard")}
        title="Cancel this appointment?"
        confirmLabel="Cancel appointment"
        cancelLabel="Keep it"
        destructive
        busy={cancel.pending}
      >
        <p className="text-sm text-ink-muted">
          The event will be removed from the clinic calendar, any pending reminders will
          be stopped, and the patient will be told on WhatsApp.
        </p>
      </ConfirmDialog>
    </>
  );
}

/**
 * Booking uses real availability rather than a free datetime field: the slot
 * list comes from the clinic's live calendar, so a staff member cannot book
 * over an existing appointment or outside working hours.
 */
function BookAppointmentModal({
  open,
  onClose,
  onBooked,
}: {
  open: boolean;
  onClose: () => void;
  onBooked: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [reason, setReason] = useState("");
  const [slot, setSlot] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const clinic = useApiQuery((signal) => clinicApi.me(signal), [open]);

  const availability = useApiQuery<Slot[]>(
    (signal) =>
      open
        ? appointmentApi.availability({ doctor_id: doctorId || undefined, limit: 30 }, signal)
        : Promise.resolve([]),
    [open, doctorId],
  );

  const book = useMutation(async () => {
    setFieldErrors({});
    try {
      const { message } = await appointmentApi.create({
        patient_name: name.trim(),
        patient_phone: phone.trim(),
        starts_at: slot,
        doctor_id: doctorId || null,
        reason: reason.trim(),
      });
      toast.showSuccess(message, "Appointment booked.");
      setName("");
      setPhone("");
      setReason("");
      setSlot("");
      onBooked();
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        if (error.details.length === 0) toast.showApiError(error);
      } else {
        toast.showApiError(error);
      }
    }
  });

  const doctorOptions = (clinic.data?.doctors ?? [])
    .filter((doctor) => doctor.is_active)
    .map((doctor) => ({ value: doctor.id, label: doctor.name }));

  const slotOptions = (availability.data ?? []).map((available) => ({
    value: available.starts_at,
    label: available.label,
  }));

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Book an appointment"
      description="Only slots that are genuinely free on the clinic calendar are offered."
      busy={book.pending}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={book.pending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => book.run()}
            loading={book.pending}
            disabled={!name.trim() || !phone.trim() || !slot}
          >
            Book
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {availability.error?.isIntegrationError && (
          <Alert tone="warning" title="Google Calendar is not connected">
            Connect it in Settings before booking, otherwise there are no slots to
            offer and the agent cannot book either.
          </Alert>
        )}

        <Field label="Patient name" required error={fieldErrors.patient_name}>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Anjali Gupta"
          />
        </Field>

        <Field
          label="Phone number"
          required
          hint="Include the country code, e.g. +919876543210."
          error={fieldErrors.patient_phone}
        >
          <Input
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="+919876543210"
            inputMode="tel"
          />
        </Field>

        {doctorOptions.length > 0 && (
          <Field label="Doctor" hint="Leave empty to use the clinic's general schedule.">
            <Select
              options={doctorOptions}
              placeholder="Any doctor"
              value={doctorId}
              onChange={(event) => {
                setDoctorId(event.target.value);
                // Slots are per-doctor, so a previously chosen one is no longer valid.
                setSlot("");
              }}
            />
          </Field>
        )}

        <Field
          label="Available slot"
          required
          error={fieldErrors.starts_at}
          hint={
            availability.loading
              ? "Checking the calendar…"
              : slotOptions.length === 0
                ? "No open slots in the next two weeks."
                : undefined
          }
        >
          <Select
            options={slotOptions}
            placeholder={availability.loading ? "Loading…" : "Choose a time"}
            value={slot}
            onChange={(event) => setSlot(event.target.value)}
            disabled={availability.loading || slotOptions.length === 0}
          />
        </Field>

        <Field label="Reason for visit" error={fieldErrors.reason}>
          <Textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Fever and cough for three days"
            rows={3}
          />
        </Field>
      </div>
    </Modal>
  );
}

function RescheduleModal({
  appointment,
  onClose,
  onDone,
}: {
  appointment: Appointment | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [when, setWhen] = useState("");
  const [reason, setReason] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const move = useMutation(async () => {
    if (!appointment) return;
    setFieldErrors({});
    try {
      const { message } = await appointmentApi.reschedule(appointment.id, {
        // A datetime-local value is naive; the API rejects those, so the
        // browser's offset is applied before sending.
        starts_at: localInputToIso(when),
        reason: reason.trim(),
      });
      toast.showSuccess(message, "Appointment moved.");
      setWhen("");
      setReason("");
      onDone();
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        if (error.details.length === 0) toast.showApiError(error);
      } else {
        toast.showApiError(error);
      }
    }
  });

  return (
    <Modal
      open={Boolean(appointment)}
      onClose={onClose}
      title="Move this appointment"
      description={
        appointment
          ? `${appointment.patient.name || "Patient"} · currently ${appointment.starts_at_local}`
          : undefined
      }
      busy={move.pending}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={move.pending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => move.run()}
            loading={move.pending}
            disabled={!when}
          >
            Move appointment
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field
          label="New date and time"
          required
          error={fieldErrors.starts_at}
          hint="The calendar is re-checked before the move is confirmed."
        >
          <Input
            type="datetime-local"
            value={when}
            min={isoToLocalInput(new Date().toISOString())}
            onChange={(event) => setWhen(event.target.value)}
          />
        </Field>

        <Field label="Reason" error={fieldErrors.reason}>
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Patient requested a later slot"
          />
        </Field>

        <p className="text-xs text-ink-subtle">
          The calendar event moves with it, the old reminders are cancelled, and the
          patient is sent an updated WhatsApp message.
        </p>
      </div>
    </Modal>
  );
}
