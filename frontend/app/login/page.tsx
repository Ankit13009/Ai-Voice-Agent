"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { Alert, Button, Field, Input } from "@/components/ui";

/** What the agent actually does, in the order a client asks about it. */
const CAPABILITIES = [
  {
    title: "Answers in Hindi and English",
    detail: "Switches mid-sentence, the way callers actually speak.",
  },
  {
    title: "Books straight into the calendar",
    detail: "Checks real availability first, so nothing is double booked.",
  },
  {
    title: "Confirms and reminds on WhatsApp",
    detail: "At booking, a day before, and two hours before.",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const { user, loading: sessionLoading, login } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Superadmins have no tenant, so every business-scoped page would 403.
  // Send them to the operator area instead.
  const homeFor = (role: string) => (role === "superadmin" ? "/admin" : "/");

  useEffect(() => {
    if (!sessionLoading && user) router.replace(homeFor(user.role));
  }, [sessionLoading, user, router]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFormError("");
    setFieldErrors({});

    try {
      const signedIn = await login(email.trim(), password);
      router.replace(homeFor(signedIn.role));
    } catch (error) {
      if (error instanceof ApiError) {
        // Field-level problems go inline; everything else becomes the banner.
        setFieldErrors(error.fieldErrors);
        if (error.details.length === 0) setFormError(error.message);
      } else {
        setFormError("Could not sign in. Please try again.");
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen grid lg:grid-cols-2 bg-canvas">
      {/* Left: what the product does. Hidden below lg, where the form is the
          only thing worth the vertical space on a phone. */}
      <section className="hidden lg:flex flex-col justify-between p-12 xl:p-16 bg-primary text-ink-inverse relative overflow-hidden">
        {/* Depth without imagery: two soft radial washes over the brand colour.
            Decorative stock photography would say nothing true about a phone
            agent, so the panel carries the product's actual claims instead. */}
        <div
          aria-hidden
          className="absolute inset-0 opacity-[0.18] pointer-events-none"
          style={{
            backgroundImage:
              "radial-gradient(60rem 40rem at 15% 5%, white, transparent 60%), radial-gradient(45rem 35rem at 95% 95%, white, transparent 55%)",
          }}
        />

        <div className="relative">
          <p className="text-sm font-semibold tracking-wide uppercase opacity-80">
            Business Receptionist
          </p>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-3xl xl:text-4xl font-semibold leading-tight">
            Your phone, answered every time.
          </h2>
          <p className="mt-4 text-base leading-relaxed opacity-90">
            An AI receptionist that picks up on the first ring, books the
            appointment, and never puts a caller on hold.
          </p>

          <ul className="mt-10 flex flex-col gap-5">
            {CAPABILITIES.map((item) => (
              <li key={item.title} className="flex gap-3">
                <span
                  aria-hidden
                  className="mt-[7px] h-1.5 w-1.5 rounded-full bg-white/70 shrink-0"
                />
                <div>
                  <p className="text-sm font-medium">{item.title}</p>
                  <p className="text-sm opacity-75 mt-0.5">{item.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs opacity-70">
          Calls are recorded and transcribed for the business that receives them.
        </p>
      </section>

      {/* Right: the form. */}
      <section className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            {/* The left panel carries the product name on desktop, but it is
                hidden below lg, which would otherwise leave a bare form with
                nothing identifying what it signs you into. */}
            <p className="lg:hidden text-xs font-semibold tracking-wide uppercase text-primary mb-3">
              Business Receptionist
            </p>
            <h1 className="text-2xl font-semibold text-ink">Sign in</h1>
            <p className="text-sm text-ink-muted mt-1.5">
              Manage your calls, appointments and reminders.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            {formError && <Alert tone="danger">{formError}</Alert>}

            <Field label="Email" required error={fieldErrors.email}>
              <Input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                placeholder="you@business.in"
                required
                autoFocus
              />
            </Field>

            <Field label="Password" required error={fieldErrors.password}>
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                placeholder="••••••••••"
                required
              />
            </Field>

            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              loading={submitting}
              className="mt-2"
            >
              Sign in
            </Button>
          </form>

          {/* No self-serve reset exists: there is no email service to deliver
              one, so the honest instruction is to ask a human. */}
          <p className="text-xs text-ink-subtle mt-6">
            Forgot your password? Your business administrator can issue a new one.
          </p>
        </div>
      </section>
    </main>
  );
}
