"use client";

import { useState } from "react";

import { integrationApi } from "@/lib/api/endpoints";
import { useApiQuery } from "@/lib/useApi";
import { Alert, Button, useToast } from "@/components/ui";

/**
 * Warns on every dashboard page while the calendar is disconnected.
 *
 * A revoked Google token is invisible from the owner's side: the agent keeps
 * answering the phone, but it can no longer read availability, so it refuses
 * every booking request. Without this banner the first sign of trouble is a
 * caller who was turned away and did not call back.
 *
 * It sits in the dashboard layout rather than on the settings page, because the
 * owner has no reason to open settings on a day when nothing appears wrong.
 */
export function CalendarStatusBanner() {
  const toast = useToast();
  const [connecting, setConnecting] = useState(false);
  const status = useApiQuery((signal) => integrationApi.googleStatus(signal), []);

  // Staff users get a 403 from this endpoint, and a new business has no
  // credential yet. Neither is worth an error banner, so stay silent unless we
  // positively know the connection is broken.
  if (status.loading || status.error || !status.data) return null;
  if (status.data.connected) return null;

  const reconnect = async () => {
    setConnecting(true);
    try {
      const { authorization_url } = await integrationApi.googleAuthorize();
      window.location.href = authorization_url;
    } catch (error) {
      toast.showApiError(error);
      setConnecting(false);
    }
  };

  return (
    <Alert tone="danger" title="Google Calendar is disconnected">
      <p className="text-sm">
        The phone assistant cannot check availability or book appointments until this
        is reconnected. Callers are being told the calendar is unavailable.
      </p>
      {status.data.last_error && (
        <p className="text-xs mt-1 opacity-80">{status.data.last_error}</p>
      )}
      <Button
        size="sm"
        variant="secondary"
        className="mt-2"
        onClick={reconnect}
        loading={connecting}
      >
        Reconnect Google Calendar
      </Button>
    </Alert>
  );
}
