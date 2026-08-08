"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { onboardingApi } from "@/lib/api/endpoints";
import { useApiQuery, useMutation } from "@/lib/useApi";
import type {
  BusinessTypePreset,
  IntakeField,
  Language,
  OnboardBusinessResponse,
  StaffMemberCreateRequest,
} from "@/types/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  Input,
  PageHeader,
  PageLoading,
  Select,
  Table,
  Textarea,
  useToast,
  cn,
  type Column,
} from "@/components/ui";

const LANGUAGES = [
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

/** Slug is derived from the name, but stays editable once touched. */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 60);
}

function generatePassword(): string {
  // Readable rather than maximally random: you will be reading it to a client
  // over the phone. Ambiguous characters (0/O, 1/l) are excluded.
  const words = ["swift", "amber", "cedar", "lunar", "coral", "ridge", "quartz", "olive"];
  const chars = "abcdefghijkmnpqrstuvwxyz23456789";
  const word = words[Math.floor(Math.random() * words.length)];
  let tail = "";
  for (let i = 0; i < 6; i++) tail += chars[Math.floor(Math.random() * chars.length)];
  return `${word}-${tail}`;
}

export default function OnboardPage() {
  const toast = useToast();
  const presetsQuery = useApiQuery((signal) => onboardingApi.businessTypes(signal), []);

  const [typeSlug, setTypeSlug] = useState("");
  const [result, setResult] = useState<OnboardBusinessResponse | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  // --- Business ---
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [address, setAddress] = useState("");
  const [city, setCity] = useState("");
  const [contactPhone, setContactPhone] = useState("");

  // --- Owner ---
  const [ownerName, setOwnerName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [ownerPassword, setOwnerPassword] = useState(generatePassword);

  // --- Agent ---
  const [agentName, setAgentName] = useState("");
  const [language, setLanguage] = useState<Language>("hi-en");
  const [greetingHi, setGreetingHi] = useState("");
  const [greetingEn, setGreetingEn] = useState("");

  // --- Hours ---
  const [opensAt, setOpensAt] = useState("09:00");
  const [closesAt, setClosesAt] = useState("19:00");
  const [days, setDays] = useState<number[]>([1, 2, 3, 4, 5, 6]);
  const [slotMinutes, setSlotMinutes] = useState("30");

  // --- Staff ---
  const [staff, setStaff] = useState<StaffMemberCreateRequest[]>([]);
  const [staffName, setStaffName] = useState("");
  const [staffSpec, setStaffSpec] = useState("");

  // --- Advanced (preset-filled, editable) ---
  const [descriptor, setDescriptor] = useState("");
  const [rules, setRules] = useState("");
  const [escalation, setEscalation] = useState("");
  const [intake, setIntake] = useState<IntakeField[]>([]);
  const [labels, setLabels] = useState({
    customer_singular: "",
    customer_plural: "",
    staff_singular: "",
    staff_plural: "",
    booking_singular: "appointment",
    booking_plural: "appointments",
  });

  const presets = presetsQuery.data ?? [];
  const preset: BusinessTypePreset | undefined = useMemo(
    () => presets.find((p) => p.slug === typeSlug),
    [presets, typeSlug],
  );

  // Choosing a type pre-fills everything it knows. Each of these stays editable,
  // which is the whole point: a preset is a starting point, not a constraint.
  useEffect(() => {
    if (!preset) return;
    setAgentName(preset.default_agent_name);
    setDescriptor(preset.business_descriptor);
    setLabels(preset.labels);
    setIntake(preset.intake_fields);
    setRules(preset.rules.join("\n"));
    setEscalation(preset.escalation);
    setSlotMinutes(String(preset.default_slot_minutes));
  }, [preset]);

  useEffect(() => {
    if (!slugTouched) setSlug(slugify(name));
  }, [name, slugTouched]);

  const toggleDay = (day: number) =>
    setDays((current) =>
      current.includes(day)
        ? current.filter((d) => d !== day)
        : [...current, day].sort((a, b) => a - b),
    );

  const addStaff = () => {
    if (!staffName.trim()) return;
    setStaff((current) => [
      ...current,
      {
        name: staffName.trim(),
        specialization: staffSpec.trim(),
        consultation_duration_minutes: Number(slotMinutes) || 30,
      },
    ]);
    setStaffName("");
    setStaffSpec("");
  };

  const submit = useMutation(async () => {
    setFieldErrors({});
    setFormError("");
    try {
      const { data } = await onboardingApi.createBusiness({
        name: name.trim(),
        slug: slug.trim(),
        phone_number: phoneNumber.trim(),
        business_type: typeSlug || "general",
        owner_email: ownerEmail.trim(),
        owner_password: ownerPassword,
        owner_name: ownerName.trim(),
        address: address.trim(),
        city: city.trim(),
        contact_phone: contactPhone.trim() || undefined,
        agent_name: agentName.trim(),
        business_descriptor: descriptor.trim(),
        primary_language: language,
        greeting_hi: greetingHi.trim(),
        greeting_en: greetingEn.trim(),
        opens_at: `${opensAt}:00`,
        closes_at: `${closesAt}:00`,
        working_days: days,
        slot_duration_minutes: Number(slotMinutes) || 30,
        staff_members: staff,
        labels,
        intake_fields: intake,
        agent_rules: rules.split("\n").map((r) => r.trim()).filter(Boolean),
        escalation_instructions: escalation.trim(),
      });
      setResult(data);
      toast.showSuccess(null, `${data.business.name} created.`);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        if (error.details.length === 0) setFormError(error.message);
        else setFormError("Some fields need fixing. See the highlighted inputs.");
      } else {
        setFormError("Could not create the client. Please try again.");
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });

  if (presetsQuery.loading) return <PageLoading />;

  // --- Success: hand the operator exactly what to do and say next ---
  if (result) {
    return (
      <>
        <PageHeader
          title={`${result.business.name} is set up`}
          description="Give the owner their login, then finish the steps below."
          action={
            <Link href="/admin">
              <Button variant="secondary">Back to clients</Button>
            </Link>
          }
        />

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader
              title="Owner login"
              description="Send these to the client. The password is not recoverable, so copy it now."
            />
            <CardBody className="flex flex-col gap-3">
              <div className="rounded-lg bg-surface-sunken p-4 font-mono text-sm flex flex-col gap-1">
                <p>
                  <span className="text-ink-subtle">Dashboard </span>
                  {typeof window !== "undefined" ? window.location.origin : ""}/login
                </p>
                <p>
                  <span className="text-ink-subtle">Email    </span>
                  {result.business.contact_email}
                </p>
                <p>
                  <span className="text-ink-subtle">Password </span>
                  {ownerPassword}
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                className="self-start"
                onClick={() => {
                  navigator.clipboard?.writeText(
                    `Dashboard: ${window.location.origin}/login\nEmail: ${result.business.contact_email}\nPassword: ${ownerPassword}`,
                  );
                  toast.showSuccess(null, "Login details copied.");
                }}
              >
                Copy details
              </Button>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Still to do"
              description="Anything onboarding could not finish automatically."
            />
            <CardBody>
              <ol className="flex flex-col gap-2">
                {result.next_steps.map((step, index) => (
                  <li key={index} className="flex gap-3 text-sm text-ink">
                    <span className="text-ink-subtle tnum shrink-0">{index + 1}.</span>
                    {step}
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>

          <Alert tone="info" title="Tell the client to set up call forwarding">
            They keep their advertised number and forward to{" "}
            <span className="font-mono">{result.business.phone_number}</span>. Dialled
            once from the business phone:{" "}
            <span className="font-mono">*61*{result.business.phone_number}#</span> forwards
            calls they do not answer. Codes vary by carrier.
          </Alert>

          <div className="flex gap-2">
            <Button
              variant="primary"
              onClick={() => {
                setResult(null);
                setName("");
                setSlug("");
                setSlugTouched(false);
                setPhoneNumber("");
                setOwnerEmail("");
                setOwnerName("");
                setOwnerPassword(generatePassword());
                setStaff([]);
              }}
            >
              Add another client
            </Button>
            <Link href="/admin">
              <Button variant="secondary">Done</Button>
            </Link>
          </div>
        </div>
      </>
    );
  }

  const staffColumns: Array<Column<StaffMemberCreateRequest>> = [
    {
      key: "name",
      header: labels.staff_singular || "Team member",
      render: (member) => (
        <div>
          <p className="text-sm text-ink">{member.name}</p>
          {member.specialization && (
            <p className="text-xs text-ink-subtle mt-0.5">{member.specialization}</p>
          )}
        </div>
      ),
    },
    {
      key: "remove",
      header: "",
      align: "right",
      render: (member) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setStaff((c) => c.filter((m) => m !== member))}
        >
          Remove
        </Button>
      ),
    },
  ];

  const canSubmit =
    Boolean(typeSlug) &&
    name.trim().length > 0 &&
    slug.trim().length > 1 &&
    phoneNumber.trim().length > 0 &&
    ownerEmail.trim().length > 0 &&
    ownerPassword.length >= 10 &&
    days.length > 0;

  return (
    <>
      <PageHeader
        title="Add a client"
        description="Pick a business type, fill in what you know, and the agent is provisioned automatically."
      />

      {formError && (
        <div className="mb-4">
          <Alert tone="danger">{formError}</Alert>
        </div>
      )}

      <div className="flex flex-col gap-4">
        {/* --- 1. Business type --- */}
        <Card>
          <CardHeader
            title="1. Business type"
            description="This pre-fills the agent's persona, vocabulary, questions and rules. All of it stays editable."
          />
          <CardBody>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {presets.map((option) => {
                const selected = option.slug === typeSlug;
                return (
                  <button
                    key={option.slug}
                    type="button"
                    onClick={() => setTypeSlug(option.slug)}
                    aria-pressed={selected}
                    className={cn(
                      "text-left rounded-lg border px-3 py-2.5 transition-colors",
                      selected
                        ? "border-primary bg-primary-soft"
                        : "border-line bg-surface hover:border-line-strong",
                    )}
                  >
                    <p
                      className={cn(
                        "text-sm font-medium",
                        selected ? "text-primary" : "text-ink",
                      )}
                    >
                      {option.display_name}
                    </p>
                    <p className="text-xs text-ink-subtle mt-0.5">
                      {option.labels.customer_plural} · {option.labels.staff_plural}
                    </p>
                  </button>
                );
              })}
            </div>

            {preset && (
              <div className="mt-4 rounded-lg bg-surface-sunken p-4 flex flex-col gap-2">
                <p className="text-xs font-medium text-ink">
                  The agent will introduce itself as {preset.default_agent_name}, for{" "}
                  {preset.business_descriptor}, and follow {preset.rules.length} rules
                  {preset.escalation ? ", with an urgent-case path" : ""}.
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {preset.example_services.map((service) => (
                    <Badge key={service} tone="neutral">
                      {service}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </CardBody>
        </Card>

        {/* --- 2. Business details --- */}
        <Card>
          <CardHeader title="2. Business details" />
          <CardBody className="grid gap-4 md:grid-cols-2">
            <Field label="Business name" required error={fieldErrors.name}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Glow Studio"
              />
            </Field>
            <Field
              label="URL slug"
              required
              hint="Used internally. Lowercase letters, numbers and hyphens."
              error={fieldErrors.slug}
            >
              <Input
                value={slug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setSlug(slugify(e.target.value));
                }}
                placeholder="glow-studio"
              />
            </Field>
            <Field
              label="AI phone number"
              required
              hint="The number their calls forward to. Include the country code."
              error={fieldErrors.phone_number}
            >
              <Input
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="+911140005678"
                inputMode="tel"
              />
            </Field>
            <Field
              label="Their public number"
              hint="What customers already dial. Used for callbacks."
              error={fieldErrors.contact_phone}
            >
              <Input
                value={contactPhone}
                onChange={(e) => setContactPhone(e.target.value)}
                placeholder="+919820011223"
                inputMode="tel"
              />
            </Field>
            <Field label="Address" className="md:col-span-2" error={fieldErrors.address}>
              <Input
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="14 Linking Road, Bandra West"
              />
            </Field>
            <Field label="City" error={fieldErrors.city}>
              <Input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Mumbai" />
            </Field>
          </CardBody>
        </Card>

        {/* --- 3. Owner login --- */}
        <Card>
          <CardHeader
            title="3. Owner login"
            description="The account the client signs in with. You will read these out to them."
          />
          <CardBody className="grid gap-4 md:grid-cols-2">
            <Field label="Owner name" error={fieldErrors.owner_name}>
              <Input
                value={ownerName}
                onChange={(e) => setOwnerName(e.target.value)}
                placeholder="Priya Nair"
              />
            </Field>
            <Field label="Email" required error={fieldErrors.owner_email}>
              <Input
                type="email"
                value={ownerEmail}
                onChange={(e) => setOwnerEmail(e.target.value)}
                placeholder="owner@glowstudio.in"
              />
            </Field>
            <Field
              label="Password"
              required
              className="md:col-span-2"
              hint="Generated to be easy to read aloud. Copy it now: it cannot be recovered later."
              error={fieldErrors.owner_password}
            >
              <div className="flex gap-2">
                <Input
                  value={ownerPassword}
                  onChange={(e) => setOwnerPassword(e.target.value)}
                  className="font-mono"
                />
                <Button
                  variant="secondary"
                  onClick={() => setOwnerPassword(generatePassword())}
                  className="shrink-0"
                >
                  Regenerate
                </Button>
              </div>
            </Field>
          </CardBody>
        </Card>

        {/* --- 4. Agent --- */}
        <Card>
          <CardHeader title="4. The agent" />
          <CardBody className="grid gap-4 md:grid-cols-2">
            <Field label="Agent name" hint="What it calls itself on the phone.">
              <Input value={agentName} onChange={(e) => setAgentName(e.target.value)} />
            </Field>
            <Field label="Language">
              <Select
                options={LANGUAGES}
                value={language}
                onChange={(e) => setLanguage(e.target.value as Language)}
              />
            </Field>
            <Field
              label="Hindi greeting"
              className="md:col-span-2"
              hint="Leave empty to generate one from the business name."
            >
              <Textarea
                value={greetingHi}
                onChange={(e) => setGreetingHi(e.target.value)}
                rows={2}
              />
            </Field>
            <Field label="English greeting" className="md:col-span-2">
              <Textarea
                value={greetingEn}
                onChange={(e) => setGreetingEn(e.target.value)}
                rows={2}
              />
            </Field>
          </CardBody>
        </Card>

        {/* --- 5. Hours --- */}
        <Card>
          <CardHeader
            title="5. Opening hours"
            description="The agent will not offer slots outside these."
          />
          <CardBody className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Opens at">
                <Input type="time" value={opensAt} onChange={(e) => setOpensAt(e.target.value)} />
              </Field>
              <Field label="Closes at">
                <Input type="time" value={closesAt} onChange={(e) => setClosesAt(e.target.value)} />
              </Field>
              <Field label="Slot length" hint="Minutes.">
                <Input
                  type="number"
                  min={5}
                  max={240}
                  value={slotMinutes}
                  onChange={(e) => setSlotMinutes(e.target.value)}
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
                      className={cn(
                        "h-9 w-14 rounded border text-sm transition-colors",
                        active
                          ? "bg-primary-soft border-primary/30 text-primary font-medium"
                          : "bg-surface border-line text-ink-muted hover:border-line-strong",
                      )}
                    >
                      {day.label}
                    </button>
                  );
                })}
              </div>
              {days.length === 0 && (
                <p className="text-xs text-danger mt-2">Select at least one day.</p>
              )}
            </div>
          </CardBody>
        </Card>

        {/* --- 6. Staff --- */}
        <Card>
          <CardHeader
            title={`6. ${labels.staff_plural || "Team"}`}
            description="Optional. Without any, the business runs one shared schedule."
          />
          <Table
            columns={staffColumns}
            rows={staff}
            rowKey={(m) => m.name}
            empty={
              <p className="text-center text-sm text-ink-subtle">
                None added. The agent will not ask for a preference.
              </p>
            }
          />
          <CardBody className="border-t border-line grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
            <Field label="Name">
              <Input
                value={staffName}
                onChange={(e) => setStaffName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addStaff()}
                placeholder="Kabir Shah"
              />
            </Field>
            <Field label="Specialization">
              <Input
                value={staffSpec}
                onChange={(e) => setStaffSpec(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addStaff()}
                placeholder="Colour Specialist"
              />
            </Field>
            <Button variant="secondary" onClick={addStaff} disabled={!staffName.trim()}>
              Add
            </Button>
          </CardBody>
        </Card>

        {/* --- 7. Advanced --- */}
        <Card>
          <CardHeader
            title="7. Agent rules and wording"
            description="Pre-filled from the business type. Open this only if the client needs something different."
            action={
              <Button variant="ghost" size="sm" onClick={() => setShowAdvanced((v) => !v)}>
                {showAdvanced ? "Hide" : "Customise"}
              </Button>
            }
          />
          {showAdvanced && (
            <CardBody className="flex flex-col gap-4">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="What the business is" hint="Completes: 'the receptionist for X, …'">
                  <Input value={descriptor} onChange={(e) => setDescriptor(e.target.value)} />
                </Field>
                <Field label={`They call customers`}>
                  <div className="flex gap-2">
                    <Input
                      value={labels.customer_singular}
                      onChange={(e) =>
                        setLabels({ ...labels, customer_singular: e.target.value })
                      }
                      placeholder="Client"
                    />
                    <Input
                      value={labels.customer_plural}
                      onChange={(e) => setLabels({ ...labels, customer_plural: e.target.value })}
                      placeholder="Clients"
                    />
                  </div>
                </Field>
                <Field label="They call staff">
                  <div className="flex gap-2">
                    <Input
                      value={labels.staff_singular}
                      onChange={(e) => setLabels({ ...labels, staff_singular: e.target.value })}
                      placeholder="Stylist"
                    />
                    <Input
                      value={labels.staff_plural}
                      onChange={(e) => setLabels({ ...labels, staff_plural: e.target.value })}
                      placeholder="Stylists"
                    />
                  </div>
                </Field>
              </div>

              <Field
                label="Rules the agent must follow"
                hint="One per line. This is where the client's liability lives."
              >
                <Textarea value={rules} onChange={(e) => setRules(e.target.value)} rows={6} />
              </Field>

              <Field
                label="What to do when something is urgent"
                hint="Leave empty if this business has no emergency path."
              >
                <Textarea
                  value={escalation}
                  onChange={(e) => setEscalation(e.target.value)}
                  rows={3}
                />
              </Field>

              {intake.length > 0 && (
                <div>
                  <p className="text-sm font-medium text-ink mb-2">
                    What the agent collects before booking
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {intake.map((f, i) => (
                      <li key={f.key} className="flex items-center gap-2 text-sm">
                        <span className="text-ink-subtle tnum">{i + 1}.</span>
                        <Input
                          value={f.label}
                          onChange={(e) => {
                            const next = [...intake];
                            next[i] = { ...f, label: e.target.value };
                            setIntake(next);
                          }}
                          className="max-w-xs"
                        />
                        <Badge tone={f.required ? "primary" : "neutral"}>
                          {f.required ? "required" : "optional"}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardBody>
          )}
        </Card>

        <div className="flex items-center gap-3 pb-8">
          <Button
            variant="primary"
            size="lg"
            onClick={() => submit.run()}
            loading={submit.pending}
            disabled={!canSubmit}
          >
            Create client
          </Button>
          <Link href="/admin">
            <Button variant="secondary" size="lg" disabled={submit.pending}>
              Cancel
            </Button>
          </Link>
          {!typeSlug && (
            <span className="text-sm text-ink-subtle">Pick a business type first.</span>
          )}
        </div>
      </div>
    </>
  );
}
