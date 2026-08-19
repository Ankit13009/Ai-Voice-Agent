"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { authApi } from "@/lib/api/endpoints";
import { useAuth } from "@/lib/auth";
import { Alert, Button, Field, Input, useToast } from "@/components/ui";

/**
 * Forced when a user still holds the one-time password they were given.
 *
 * The server now refuses every other endpoint until this is done, so this page
 * is the only thing such a user can reach. Without it they would sign in
 * successfully and then meet a permission error on every screen, with nothing
 * telling them what to do about it.
 */
export default function ChangePasswordPage() {
  const router = useRouter();
  const toast = useToast();
  const { user, refreshUser } = useAuth();
  // The same screen serves two cases: forced after an admin reset, and chosen
  // voluntarily from the sidebar. Telling someone who came here on purpose that
  // they "signed in with a one-time password" would be wrong.
  const forced = Boolean(user?.must_change_password);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      setFormError("The two new passwords do not match.");
      return;
    }
    setSubmitting(true);
    setFormError("");

    try {
      await authApi.changePassword({ current_password: current, new_password: next });
      toast.showSuccess(null, "Password updated.");
      // The flag lives on the user record, so the session has to be re-read
      // before the rest of the dashboard will let them through.
      await refreshUser();
      router.replace(user?.role === "superadmin" ? "/admin" : "/");
    } catch (error) {
      setFormError(
        error instanceof ApiError ? error.message : "Could not update the password.",
      );
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-6 bg-canvas">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-semibold text-ink">
          {forced ? "Set your password" : "Change your password"}
        </h1>
        <p className="text-sm text-ink-muted mt-1.5 mb-6">
          {forced
            ? "You signed in with a one-time password. Choose your own before continuing."
            : "Choose a new password. You will stay signed in on this device."}
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          {formError && <Alert tone="danger">{formError}</Alert>}

          <Field label={forced ? "One-time password" : "Current password"} required>
            <Input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              autoComplete="current-password"
              required
              autoFocus
            />
          </Field>

          <Field label="New password" required hint="At least 10 characters.">
            <Input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
          </Field>

          <Field label="Confirm new password" required>
            <Input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
          </Field>

          <Button type="submit" variant="primary" size="lg" fullWidth loading={submitting}>
            Save and continue
          </Button>
        </form>
      </div>
    </main>
  );
}
