"use client";

import { useMemo, useState } from "react";

import { messageApi } from "@/lib/api/endpoints";
import { useApiList, useMutation } from "@/lib/useApi";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { MessageKind, MessageStatus, WhatsAppMessage } from "@/types/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CellStack,
  EmptyState,
  MessageStatusBadge,
  PageHeader,
  Pagination,
  Select,
  Table,
  useToast,
  type Column,
} from "@/components/ui";

const KIND_LABELS: Record<MessageKind, string> = {
  confirmation: "Confirmation",
  reminder_24h: "24h reminder",
  reminder_2h: "2h reminder",
  cancellation: "Cancellation",
  reschedule: "Reschedule",
};

const KIND_OPTIONS = Object.entries(KIND_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const STATUS_OPTIONS = [
  { value: "pending", label: "Queued" },
  { value: "sent", label: "Sent" },
  { value: "delivered", label: "Delivered" },
  { value: "read", label: "Read" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

export default function MessagesPage() {
  const toast = useToast();
  const [status, setStatus] = useState<MessageStatus | "">("");
  const [kind, setKind] = useState<MessageKind | "">("");

  const list = useApiList<WhatsAppMessage>(
    (page, signal) =>
      messageApi.list(
        {
          page,
          page_size: 20,
          status: status || undefined,
          kind: kind || undefined,
        },
        signal,
      ),
    `${status}|${kind}`,
  );

  const retry = useMutation(async (message: WhatsAppMessage) => {
    try {
      const { message: text } = await messageApi.retry(message.id);
      toast.showSuccess(text, "Message sent.");
      list.refetch();
    } catch (error) {
      toast.showApiError(error, "Could not resend the message.");
    }
  });

  const columns = useMemo<Array<Column<WhatsAppMessage>>>(
    () => [
      {
        key: "to",
        header: "To",
        render: (message) => (
          <CellStack
            primary={formatPhone(message.to_phone)}
            secondary={KIND_LABELS[message.kind] ?? message.kind}
          />
        ),
      },
      {
        key: "preview",
        header: "Message",
        hideOnMobile: true,
        render: (message) => (
          <p className="text-sm text-ink-muted line-clamp-2 max-w-md">
            {message.rendered_preview}
          </p>
        ),
      },
      {
        key: "when",
        header: "Scheduled / sent",
        hideOnMobile: true,
        render: (message) => (
          <span className="text-sm text-ink-muted tnum">
            {formatDateTime(message.sent_at ?? message.scheduled_for)}
          </span>
        ),
      },
      {
        key: "status",
        header: "Status",
        render: (message) => (
          <div className="flex flex-col items-start gap-1">
            <MessageStatusBadge status={message.status} />
            {message.last_error && (
              // The reason is nearly always actionable (template not approved,
              // number not on WhatsApp), so it is shown rather than hidden.
              <span
                className="text-2xs text-danger max-w-[16rem] truncate"
                title={message.last_error}
              >
                {message.last_error}
              </span>
            )}
          </div>
        ),
      },
      {
        key: "actions",
        header: "",
        align: "right",
        render: (message) =>
          message.status === "failed" ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => retry.run(message)}
              disabled={retry.pending}
            >
              Retry
            </Button>
          ) : null,
      },
    ],
    [retry],
  );

  return (
    <>
      <PageHeader
        title="WhatsApp"
        description="Confirmations and reminders, sent through Meta's Cloud API."
      />

      <Alert tone="info" title="How reminders work">
        A confirmation goes out as soon as an appointment is booked, then reminders 24
        hours and 2 hours before. Cancelling or moving an appointment automatically
        stops the reminders that are still queued.
      </Alert>

      <Card className="mt-4">
        <div className="flex flex-wrap items-center gap-3 px-5 py-3 border-b border-line">
          <Select
            className="max-w-[12rem]"
            options={STATUS_OPTIONS}
            placeholder="All statuses"
            value={status}
            onChange={(event) => setStatus(event.target.value as MessageStatus | "")}
            aria-label="Filter by status"
          />
          <Select
            className="max-w-[12rem]"
            options={KIND_OPTIONS}
            placeholder="All types"
            value={kind}
            onChange={(event) => setKind(event.target.value as MessageKind | "")}
            aria-label="Filter by message type"
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
          rowKey={(message) => message.id}
          loading={list.loading}
          empty={
            <EmptyState
              title="No messages yet"
              description="Messages appear here as soon as the first appointment is booked."
            />
          }
        />

        {list.meta && <Pagination meta={list.meta} onPageChange={list.setPage} />}
      </Card>

      <p className="text-xs text-ink-subtle mt-4">
        Templates must be approved by Meta before they can be sent. Utility templates
        cost roughly ₹0.115 each, and are free inside a 24-hour window opened by a
        customer replying.
      </p>
    </>
  );
}
