"use client";

import { useState } from "react";

import { integrationApi } from "@/lib/api/endpoints";
import { useApiQuery, useMutation } from "@/lib/useApi";
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
  useToast,
} from "@/components/ui";

/**
 * Which WhatsApp number this business's messages come from.
 *
 * Two ways to run this. By default every business sends from the platform's
 * shared number, which means a salon owner gets reminders working without
 * completing Meta's business verification. A business that would rather its
 * patients saw its own name connects its own Meta account here.
 *
 * The token is write-only: it is stored encrypted and never returned, so this
 * form can say whether one is set and show the number Meta reports, but cannot
 * show the credential itself.
 */
export function WhatsAppSenderCard() {
  const toast = useToast();
  const status = useApiQuery((signal) => integrationApi.whatsappSender(signal), []);
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [accessToken, setAccessToken] = useState("");

  const save = useMutation(async () => {
    const payload: { phone_number_id?: string; access_token?: string } = {};
    if (phoneNumberId.trim()) payload.phone_number_id = phoneNumberId.trim();
    if (accessToken.trim()) payload.access_token = accessToken.trim();

    if (Object.keys(payload).length === 0) {
      toast.show("Fill in both fields to connect your own number.", "error");
      return;
    }
    try {
      const { message } = await integrationApi.saveWhatsappSender(payload);
      toast.showSuccess(message, "Saved.");
      setPhoneNumberId("");
      setAccessToken("");
      status.refetch();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const disconnect = useMutation(async () => {
    try {
      const { message } = await integrationApi.saveWhatsappSender({ access_token: "" });
      toast.showSuccess(message, "Back to the shared number.");
      status.refetch();
    } catch (error) {
      toast.showApiError(error);
    }
  });

  const data = status.data;
  const own = Boolean(data?.using_own_number);

  return (
    <Card>
      <CardHeader
        title="Who messages come from"
        description="The number your customers see on confirmations and reminders. This is separate from the number they call."
      />

      <CardBody className="flex flex-col gap-4">
        {own ? (
          <Alert tone="success" title="Sending from your own number">
            <p className="text-sm">
              Customers see{" "}
              <span className="font-medium">{data?.display_number || "your number"}</span>{" "}
              on every confirmation and reminder.
            </p>
          </Alert>
        ) : (
          <Alert tone="info" title="Sending from the shared number">
            <p className="text-sm">
              Messages go out from our WhatsApp number, with your business name in the
              text. Connect your own below if you would rather customers saw your
              number and could reply to you directly.
            </p>
          </Alert>
        )}

        <Field
          label="Phone number ID"
          hint="From WhatsApp → API Setup in your Meta account. A long number, not the phone number itself."
        >
          <Input
            value={phoneNumberId}
            onChange={(e) => setPhoneNumberId(e.target.value)}
            placeholder={data?.phone_number_id || "e.g. 123456789012345"}
            autoComplete="off"
          />
        </Field>

        <Field
          label="Access token"
          hint="A System User token with no expiry. We store it encrypted and can never show it again."
        >
          <Input
            type="password"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder={data?.has_access_token ? "Saved" : "Not set"}
            autoComplete="off"
          />
        </Field>

        <p className="text-xs text-ink-subtle">
          A number connected here can no longer be used in the normal WhatsApp app, so
          use a spare number rather than the one your staff message customers from. We
          check the details with Meta before saving, so a wrong value is caught now
          rather than on your first appointment.
        </p>
      </CardBody>

      <CardFooter className="flex flex-wrap gap-2 justify-between">
        {own ? (
          <Button variant="ghost" onClick={() => disconnect.run()} loading={disconnect.pending}>
            Use the shared number instead
          </Button>
        ) : (
          <span />
        )}
        <Button variant="primary" onClick={() => save.run()} loading={save.pending}>
          {own ? "Update" : "Connect my number"}
        </Button>
      </CardFooter>
    </Card>
  );
}
