"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { Alert, Button, Card, Field, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading: sessionLoading, login } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // An already-signed-in user landing here goes straight to the dashboard.
  useEffect(() => {
    if (!sessionLoading && user) router.replace("/");
  }, [sessionLoading, user, router]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFormError("");
    setFieldErrors({});

    try {
      await login(email.trim(), password);
      router.replace("/");
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
    <main className="min-h-screen flex items-center justify-center p-4 bg-canvas">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <h1 className="text-xl font-semibold text-ink">Clinic Receptionist</h1>
          <p className="text-sm text-ink-muted mt-1">
            Sign in to manage calls and appointments.
          </p>
        </div>

        <Card className="p-6">
          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            {formError && <Alert tone="danger">{formError}</Alert>}

            <Field label="Email" required error={fieldErrors.email}>
              <Input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                placeholder="you@clinic.in"
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
            >
              Sign in
            </Button>
          </form>
        </Card>

        <p className="text-center text-xs text-ink-subtle mt-4">
          Trouble signing in? Contact your clinic administrator.
        </p>
      </div>
    </main>
  );
}
