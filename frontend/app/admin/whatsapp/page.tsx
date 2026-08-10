"use client";

import { useState } from "react";

import { adminApi } from "@/lib/api/endpoints";
import { useApiQuery, useMutation } from "@/lib/useApi";
import type { WhatsAppTestResult } from "@/types/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  Field,
  Input,
  PageHeader,
  PageLoading,
  useToast,
} from "@/components/ui";

/**
 * WhatsApp setup, without a deploy.
 *
 * These five values used to be environment variables, which meant connecting a
 * client's WhatsApp required whoever holds the hosting login. That put a
 * developer in the middle of every onboarding.
 *
 * Saved values are never read back: the form shows only whether each is set and
 * the last four characters, so a screenshot or a screen-share does not leak a
 * live token. Leaving a field blank keeps whatever is already stored.
 */
const FIELDS = [
  {
    key: "whatsapp_phone_number_id",
    label: "Phone number ID",
    hint: "WhatsApp → API Setup. A long number, not the phone number itself.",
    secret: false,
  },
  {
    key: "whatsapp_business_account_id",
    label: "WhatsApp Business Account ID",
    hint: "On the same API Setup screen, just below the phone number ID.",
    secret: false,
  },
  {
    key: "whatsapp_access_token",
    label: "Access token",
    hint: "Use a System User token with no expiry. The one on API Setup lasts 24 hours.",
    secret: true,
  },
  {
    key: "whatsapp_app_secret",
    label: "App secret",
    hint: "App Settings → Basic. Used to verify that webhooks really came from Meta.",
    secret: true,
  },
  {
    key: "whatsapp_verify_token",
    label: "Verify token",
    hint: "Any phrase you invent. Paste the same one into Meta's webhook setup.",
    secret: false,
  },
] as const;

export default function WhatsAppSetupPage() {
  const toast = useToast();
  const status = useApiQuery((signal) => adminApi.whatsappStatus(signal), []);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<WhatsAppTestResult | null>(null);

  const save = useMutation(async () => {
    // Only send what was actually typed. Sending empty strings would clear
    // stored credentials, which is the opposite of what a partial edit means.
    const payload = Object.fromEntries(
      Object.entries(values).filter(([, v]) => v.trim() !== ""),
    );
    if (Object.keys(payload).length === 0) {
      toast.show("Nothing to save. Fill in at least one field.", "error");
      return;
    }
    try {
      const { message } = await adminApi.saveWhatsapp(payload);
      toast.showSuccess(message, "Saved.");
      setValues({});
      setResult(null);
      status.refetch();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const test = useMutation(async () => {
    try {
      const { data } = await adminApi.testWhatsapp();
      setResult(data);
    } catch (error) {
      toast.showApiError(error);
    }
  });

  if (status.loading) return <PageLoading />;

  const settings = status.data?.settings ?? {};
  const allSet = FIELDS.every((f) => settings[f.key]?.set);

  return (
    <>
      <PageHeader
        title="WhatsApp"
        description="Connect the Meta account that sends confirmations and reminders."
      />

      <div className="max-w-2xl flex flex-col gap-4">
        {!allSet && (
          <Alert tone="warning" title="Not connected yet">
            Confirmations and reminders are queued but cannot be sent until all five
            values are filled in and every template is approved by Meta.
          </Alert>
        )}

        {result && (
          <Alert
            tone={result.ok ? "success" : "danger"}
            title={result.ok ? "Connected" : "Could not connect"}
          >
            <p className="text-sm">{result.detail}</p>
            {result.ok && result.phone_number && (
              <p className="text-xs mt-1 opacity-80">
                Messages will be sent from {result.phone_number}
                {result.quality_rating ? ` · quality ${result.quality_rating}` : ""}
              </p>
            )}
            {result.ok && result.templates_approved === 0 && (
              <p className="text-xs mt-1">
                No templates are approved yet, so nothing can send. Submit them and
                check back in a few hours.
              </p>
            )}
          </Alert>
        )}

        <Card>
          <CardHeader
            title="Credentials"
            description="From developers.facebook.com. Leave a field blank to keep what is already saved."
          />
          <CardBody className="flex flex-col gap-4">
            {FIELDS.map((field) => {
              const current = settings[field.key];
              return (
                <Field key={field.key} label={field.label} hint={field.hint}>
                  <div className="flex items-center gap-2">
                    <Input
                      type={field.secret ? "password" : "text"}
                      value={values[field.key] ?? ""}
                      onChange={(e) =>
                        setValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                      }
                      placeholder={
                        current?.set
                          ? field.secret
                            ? `Saved (${current.preview})`
                            : current.preview
                          : "Not set"
                      }
                      autoComplete="off"
                    />
                    {current?.set ? (
                      <Badge tone="success" dot>
                        Set
                      </Badge>
                    ) : (
                      <Badge tone="warning">Missing</Badge>
                    )}
                  </div>
                </Field>
              );
            })}
          </CardBody>
          <CardFooter className="flex flex-wrap gap-2 justify-between">
            <Button
              variant="secondary"
              onClick={() => test.run()}
              loading={test.pending}
            >
              Test connection
            </Button>
            <Button variant="primary" onClick={() => save.run()} loading={save.pending}>
              Save
            </Button>
          </CardFooter>
        </Card>

        <Card>
          <CardHeader
            title="Where to find these"
            description="Three different Meta screens, which is the main reason this is fiddly."
          />
          <CardBody className="text-sm text-ink-muted flex flex-col gap-2">
            <p>
              <b>1.</b> Create a Business app at developers.facebook.com and add the
              WhatsApp product.
            </p>
            <p>
              <b>2.</b> WhatsApp → API Setup gives you the phone number ID and the
              business account ID.
            </p>
            <p>
              <b>3.</b> App Settings → Basic gives you the app secret.
            </p>
            <p>
              <b>4.</b> business.facebook.com → System Users: create one with Admin
              access and generate a token that never expires. The token shown on API
              Setup expires after 24 hours.
            </p>
            <p className="text-warning">
              A number used here can no longer be used in the normal WhatsApp app. Use
              a spare SIM, never a personal or existing business number.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
