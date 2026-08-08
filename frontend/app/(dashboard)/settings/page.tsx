"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { clinicApi, integrationApi } from "@/lib/api/endpoints";
import { useApiQuery, useMutation } from "@/lib/useApi";
import { formatTime, formatWorkingDays } from "@/lib/format";
import type { Clinic, Language } from "@/types/api";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  ConfirmDialog,
  Field,
  Input,
  PageHeader,
  PageLoading,
  Select,
  Switch,
  Table,
  Textarea,
  useToast,
  type Column,
} from "@/components/ui";
import type { Doctor } from "@/types/api";

const LANGUAGE_OPTIONS = [
  { value: "hi-en", label: "Hindi + English (recommended)" },
  { value: "hi", label: "Hindi" },
  { value: "en", label: "English" },
];

const WEEKDAYS = [
  { value: 1, label: "Mon" },
  { value: 2, label: "Tue" },
  { value: 3, label: "Wed" },
  { value: 4, label: "Thu" },
  { value: 5, label: "Fri" },
  { value: 6, label: "Sat" },
  { value: 7, label: "Sun" },
];

export default function SettingsPage() {
  const toast = useToast();
  const searchParams = useSearchParams();
  const clinic = useApiQuery((signal) => clinicApi.me(signal), []);

  // The Google OAuth callback redirects back here with a status flag, since a
  // browser redirect cannot carry an Authorization header to report its result.
  useEffect(() => {
    const googleResult = searchParams.get("google");
    if (!googleResult) return;
    if (googleResult === "connected") {
      toast.showSuccess("Google Calendar connected.");
      clinic.refetch();
    } else if (googleResult === "denied") {
      toast.show("Google Calendar access was declined.", "error");
    } else if (googleResult) {
      toast.show("Could not connect Google Calendar. Please try again.", "error");
    }
    window.history.replaceState({}, "", "/settings");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  if (clinic.loading) return <PageLoading />;
  if (clinic.error || !clinic.data) {
    return (
      <Alert tone="danger" title="Could not load settings">
        {clinic.error?.message ?? "Please refresh the page."}
      </Alert>
    );
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description={`${clinic.data.name} · patients dial ${clinic.data.phone_number}`}
      />

      <div className="flex flex-col gap-4">
        <IntegrationsCard clinic={clinic.data} onChange={clinic.refetch} />
        <AgentCard clinic={clinic.data} onSaved={clinic.refetch} />
        <ScheduleCard clinic={clinic.data} onSaved={clinic.refetch} />
        <RemindersCard clinic={clinic.data} onSaved={clinic.refetch} />
        <DoctorsCard clinic={clinic.data} onChange={clinic.refetch} />
      </div>
    </>
  );
}

function IntegrationsCard({
  clinic,
  onChange,
}: {
  clinic: Clinic;
  onChange: () => void;
}) {
  const toast = useToast();
  const [disconnecting, setDisconnecting] = useState(false);
  const integrations = clinic.integrations;

  const connect = useMutation(async () => {
    try {
      const { authorization_url } = await integrationApi.googleAuthorize();
      // Full navigation, not a popup: Google blocks its consent screen in many
      // popup contexts, and the redirect back lands on this same page.
      window.location.href = authorization_url;
    } catch (error) {
      toast.showApiError(error, "Could not start the Google connection.");
    }
  });

  const disconnect = useMutation(async () => {
    try {
      const { message } = await integrationApi.googleDisconnect();
      toast.showSuccess(message, "Google Calendar disconnected.");
      setDisconnecting(false);
      onChange();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  return (
    <Card>
      <CardHeader
        title="Integrations"
        description="The agent needs these to check slots, book, and send reminders."
      />
      <CardBody className="flex flex-col gap-3">
        <IntegrationRow
          name="Google Calendar"
          connected={Boolean(integrations?.google_calendar_connected)}
          detail={
            integrations?.google_calendar_connected
              ? integrations.google_calendar_email
              : "Not connected. The agent cannot check availability or book."
          }
          error={integrations?.google_calendar_error}
          action={
            integrations?.google_calendar_connected ? (
              <Button size="sm" variant="ghost" onClick={() => setDisconnecting(true)}>
                Disconnect
              </Button>
            ) : (
              <Button
                size="sm"
                variant="primary"
                onClick={() => connect.run()}
                loading={connect.pending}
              >
                Connect
              </Button>
            )
          }
        />

        <IntegrationRow
          name="Voice agent"
          connected={Boolean(integrations?.vapi_assistant_configured)}
          detail={
            integrations?.vapi_assistant_configured
              ? "Answering calls on the clinic number."
              : "No assistant attached. Inbound calls will not be answered."
          }
        />

        <IntegrationRow
          name="WhatsApp"
          connected={Boolean(integrations?.whatsapp_configured)}
          detail={
            integrations?.whatsapp_configured
              ? "Confirmations and reminders are active."
              : "Not configured. Confirmations and reminders will not send."
          }
        />
      </CardBody>

      <ConfirmDialog
        open={disconnecting}
        onClose={() => setDisconnecting(false)}
        onConfirm={() => disconnect.run()}
        title="Disconnect Google Calendar?"
        confirmLabel="Disconnect"
        destructive
        busy={disconnect.pending}
      >
        <p className="text-sm text-ink-muted">
          The agent will immediately stop being able to check availability, book, move,
          or cancel appointments. Existing calendar events are left untouched.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

function IntegrationRow({
  name,
  connected,
  detail,
  error,
  action,
}: {
  name: string;
  connected: boolean;
  detail: string;
  error?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-line last:border-0">
      <div className="min-w-0 flex items-start gap-3">
        <span
          className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${
            connected ? "bg-success" : "bg-warning"
          }`}
          aria-hidden="true"
        />
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">{name}</p>
          <p className="text-xs text-ink-subtle mt-0.5">{detail}</p>
          {error && <p className="text-xs text-danger mt-0.5">{error}</p>}
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

function AgentCard({ clinic, onSaved }: { clinic: Clinic; onSaved: () => void }) {
  const toast = useToast();
  const [agentName, setAgentName] = useState(clinic.agent_name);
  const [language, setLanguage] = useState<Language>(clinic.primary_language);
  const [greetingHi, setGreetingHi] = useState(clinic.greeting_hi);
  const [greetingEn, setGreetingEn] = useState(clinic.greeting_en);
  const [notes, setNotes] = useState(clinic.agent_notes);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const save = useMutation(async () => {
    setFieldErrors({});
    try {
      const { message } = await clinicApi.update({
        agent_name: agentName.trim(),
        primary_language: language,
        greeting_hi: greetingHi,
        greeting_en: greetingEn,
        agent_notes: notes,
      });
      toast.showSuccess(message, "Agent settings saved.");
      onSaved();
    } catch (error) {
      if (error instanceof ApiError) setFieldErrors(error.fieldErrors);
      toast.showApiError(error);
    }
  });

  return (
    <Card>
      <CardHeader
        title="Voice agent"
        description="Saving here re-pushes the assistant, so the next call uses the new settings."
      />
      <CardBody className="grid gap-4 md:grid-cols-2">
        <Field label="Agent name" hint="What the agent calls itself on the phone.">
          <Input value={agentName} onChange={(event) => setAgentName(event.target.value)} />
        </Field>

        <Field
          label="Language"
          hint="Hindi + English lets the agent follow whichever the caller uses."
        >
          <Select
            options={LANGUAGE_OPTIONS}
            value={language}
            onChange={(event) => setLanguage(event.target.value as Language)}
          />
        </Field>

        <Field
          label="Hindi greeting"
          className="md:col-span-2"
          hint="Leave empty to use a generated greeting."
          error={fieldErrors.greeting_hi}
        >
          <Textarea
            value={greetingHi}
            onChange={(event) => setGreetingHi(event.target.value)}
            rows={2}
            placeholder="नमस्ते, ... में आपका स्वागत है।"
          />
        </Field>

        <Field
          label="English greeting"
          className="md:col-span-2"
          error={fieldErrors.greeting_en}
        >
          <Textarea
            value={greetingEn}
            onChange={(event) => setGreetingEn(event.target.value)}
            rows={2}
            placeholder="Thank you for calling ..."
          />
        </Field>

        <Field
          label="Facts the agent may state"
          className="md:col-span-2"
          hint="Parking, insurance, walk-in policy. The agent never invents anything beyond this."
          error={fieldErrors.agent_notes}
        >
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} />
        </Field>
      </CardBody>
      <CardFooter>
        <Button variant="primary" onClick={() => save.run()} loading={save.pending}>
          Save
        </Button>
      </CardFooter>
    </Card>
  );
}

function ScheduleCard({ clinic, onSaved }: { clinic: Clinic; onSaved: () => void }) {
  const toast = useToast();
  const [opensAt, setOpensAt] = useState(clinic.opens_at.slice(0, 5));
  const [closesAt, setClosesAt] = useState(clinic.closes_at.slice(0, 5));
  const [days, setDays] = useState<number[]>(clinic.working_days);
  const [slot, setSlot] = useState(String(clinic.slot_duration_minutes));

  const toggleDay = (day: number) =>
    setDays((current) =>
      current.includes(day)
        ? current.filter((value) => value !== day)
        : [...current, day].sort((a, b) => a - b),
    );

  const save = useMutation(async () => {
    try {
      const { message } = await clinicApi.update({
        opens_at: `${opensAt}:00`,
        closes_at: `${closesAt}:00`,
        working_days: days,
        slot_duration_minutes: Number(slot),
      });
      toast.showSuccess(message, "Schedule saved.");
      onSaved();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  return (
    <Card>
      <CardHeader
        title="Opening hours"
        description={`Currently ${formatWorkingDays(clinic.working_days)}, ${formatTime(clinic.opens_at)} to ${formatTime(clinic.closes_at)}.`}
      />
      <CardBody className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Opens at">
            <Input
              type="time"
              value={opensAt}
              onChange={(event) => setOpensAt(event.target.value)}
            />
          </Field>
          <Field label="Closes at">
            <Input
              type="time"
              value={closesAt}
              onChange={(event) => setClosesAt(event.target.value)}
            />
          </Field>
          <Field label="Slot length" hint="Minutes per appointment slot.">
            <Input
              type="number"
              min={5}
              max={240}
              value={slot}
              onChange={(event) => setSlot(event.target.value)}
            />
          </Field>
        </div>

        <div>
          <p className="text-sm font-medium text-ink mb-2">Working days</p>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((day) => {
              const active = days.includes(day.value);
              return (
                <button
                  key={day.value}
                  type="button"
                  onClick={() => toggleDay(day.value)}
                  aria-pressed={active}
                  className={`h-9 w-14 rounded border text-sm transition-colors ${
                    active
                      ? "bg-primary-soft border-primary/30 text-primary font-medium"
                      : "bg-surface border-line text-ink-muted hover:border-line-strong"
                  }`}
                >
                  {day.label}
                </button>
              );
            })}
          </div>
          {days.length === 0 && (
            <p className="text-xs text-danger mt-2">Select at least one working day.</p>
          )}
        </div>
      </CardBody>
      <CardFooter>
        <Button
          variant="primary"
          onClick={() => save.run()}
          loading={save.pending}
          disabled={days.length === 0}
        >
          Save
        </Button>
      </CardFooter>
    </Card>
  );
}

function RemindersCard({ clinic, onSaved }: { clinic: Clinic; onSaved: () => void }) {
  const toast = useToast();
  const [enabled, setEnabled] = useState(clinic.whatsapp_enabled);
  const [reminder24, setReminder24] = useState(clinic.reminder_24h_enabled);
  const [reminder2, setReminder2] = useState(clinic.reminder_2h_enabled);

  const save = useMutation(async () => {
    try {
      const { message } = await clinicApi.update({
        whatsapp_enabled: enabled,
        reminder_24h_enabled: reminder24,
        reminder_2h_enabled: reminder2,
      });
      toast.showSuccess(message, "Reminder settings saved.");
      onSaved();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  return (
    <Card>
      <CardHeader
        title="WhatsApp reminders"
        description="Each message is a Meta utility template, charged per send."
      />
      <CardBody className="flex flex-col divide-y divide-line">
        <Switch
          checked={enabled}
          onChange={setEnabled}
          label="WhatsApp messaging"
          description="Turning this off stops confirmations and both reminders."
        />
        <Switch
          checked={reminder24}
          onChange={setReminder24}
          disabled={!enabled}
          label="24-hour reminder"
          description="Sent the day before the appointment."
        />
        <Switch
          checked={reminder2}
          onChange={setReminder2}
          disabled={!enabled}
          label="2-hour reminder"
          description="Sent shortly before the appointment."
        />
      </CardBody>
      <CardFooter>
        <Button variant="primary" onClick={() => save.run()} loading={save.pending}>
          Save
        </Button>
      </CardFooter>
    </Card>
  );
}

function DoctorsCard({ clinic, onChange }: { clinic: Clinic; onChange: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [specialization, setSpecialization] = useState("");
  const [calendarId, setCalendarId] = useState("");
  const [duration, setDuration] = useState("15");

  const add = useMutation(async () => {
    try {
      const { message } = await clinicApi.createDoctor({
        name: name.trim(),
        specialization: specialization.trim(),
        google_calendar_id: calendarId.trim(),
        consultation_duration_minutes: Number(duration),
      });
      toast.showSuccess(message, "Doctor added.");
      setName("");
      setSpecialization("");
      setCalendarId("");
      onChange();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const deactivate = useMutation(async (doctor: Doctor) => {
    try {
      const { message } = await clinicApi.deactivateDoctor(doctor.id);
      toast.showSuccess(message, "Doctor deactivated.");
      onChange();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const columns: Array<Column<Doctor>> = [
    {
      key: "name",
      header: "Doctor",
      render: (doctor) => (
        <div>
          <p className="text-sm text-ink">{doctor.name}</p>
          {doctor.specialization && (
            <p className="text-xs text-ink-subtle mt-0.5">{doctor.specialization}</p>
          )}
        </div>
      ),
    },
    {
      key: "duration",
      header: "Slot",
      align: "right",
      hideOnMobile: true,
      render: (doctor) => (
        <span className="text-sm text-ink-muted tnum">
          {doctor.consultation_duration_minutes} min
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (doctor) =>
        doctor.is_active ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => deactivate.run(doctor)}
            disabled={deactivate.pending}
          >
            Deactivate
          </Button>
        ) : (
          <span className="text-xs text-ink-subtle">Inactive</span>
        ),
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Doctors"
        description="Give each doctor their own Google Calendar to book them separately."
      />

      <Table
        columns={columns}
        rows={clinic.doctors}
        rowKey={(doctor) => doctor.id}
        empty={
          <p className="text-center text-sm text-ink-subtle">
            No doctors yet. The clinic uses one shared schedule until you add some.
          </p>
        }
      />

      <CardBody className="border-t border-line grid gap-4 md:grid-cols-2">
        <Field label="Name">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Dr. Meera Sharma"
          />
        </Field>
        <Field label="Specialization">
          <Input
            value={specialization}
            onChange={(event) => setSpecialization(event.target.value)}
            placeholder="General Physician"
          />
        </Field>
        <Field
          label="Google Calendar ID"
          hint="Leave empty to use the clinic's main calendar."
        >
          <Input
            value={calendarId}
            onChange={(event) => setCalendarId(event.target.value)}
            placeholder="doctor@clinic.in"
          />
        </Field>
        <Field label="Consultation length" hint="Minutes.">
          <Input
            type="number"
            min={5}
            max={240}
            value={duration}
            onChange={(event) => setDuration(event.target.value)}
          />
        </Field>
      </CardBody>
      <CardFooter>
        <Button
          variant="primary"
          onClick={() => add.run()}
          loading={add.pending}
          disabled={!name.trim()}
        >
          Add doctor
        </Button>
      </CardFooter>
    </Card>
  );
}
