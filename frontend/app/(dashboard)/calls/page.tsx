"use client";

import { useEffect, useMemo, useState } from "react";

import { callApi } from "@/lib/api/endpoints";
import { useApiList, useApiQuery } from "@/lib/useApi";
import { formatDateTime, formatDuration, formatPaise, formatPhone } from "@/lib/format";
import type { Call, CallOutcome } from "@/types/api";
import {
  Alert,
  CallOutcomeBadge,
  Card,
  CellStack,
  EmptyState,
  Input,
  LanguageBadge,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Table,
  type Column,
} from "@/components/ui";

const OUTCOME_OPTIONS = [
  { value: "booked", label: "Booked" },
  { value: "rescheduled", label: "Rescheduled" },
  { value: "cancelled", label: "Cancelled" },
  { value: "enquiry", label: "Enquiry" },
  { value: "no_details", label: "No details" },
  { value: "failed", label: "Failed" },
];

export default function CallsPage() {
  const [outcome, setOutcome] = useState<CallOutcome | "">("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // A stable key so the list resets to page 1 whenever a filter changes.
  const filterKey = `${outcome}|${search}`;

  const list = useApiList<Call>(
    (page, signal) =>
      callApi.list(
        {
          page,
          page_size: 20,
          outcome: outcome || undefined,
          search: search.trim() || undefined,
        },
        signal,
      ),
    filterKey,
  );

  const columns = useMemo<Array<Column<Call>>>(
    () => [
      {
        key: "caller",
        header: "Caller",
        render: (call) => (
          <CellStack
            primary={call.customer?.name || formatPhone(call.caller_number)}
            secondary={call.customer ? formatPhone(call.caller_number) : undefined}
          />
        ),
      },
      {
        key: "when",
        header: "When",
        hideOnMobile: true,
        render: (call) => (
          <span className="text-sm text-ink-muted">
            {formatDateTime(call.started_at ?? call.created_at)}
          </span>
        ),
      },
      {
        key: "duration",
        header: "Length",
        align: "right",
        hideOnMobile: true,
        render: (call) => (
          <span className="text-sm text-ink-muted tnum">
            {formatDuration(call.duration_seconds)}
          </span>
        ),
      },
      {
        key: "language",
        header: "Language",
        hideOnMobile: true,
        render: (call) => <LanguageBadge language={call.language} />,
      },
      {
        key: "outcome",
        header: "Outcome",
        render: (call) => <CallOutcomeBadge outcome={call.outcome} />,
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Calls"
        description="Every call the AI receptionist answered, with full transcripts."
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 px-5 py-3 border-b border-line">
          <Input
            className="max-w-xs"
            placeholder="Search by phone number"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search calls by phone number"
          />
          <Select
            className="max-w-[12rem]"
            options={OUTCOME_OPTIONS}
            placeholder="All outcomes"
            value={outcome}
            onChange={(event) => setOutcome(event.target.value as CallOutcome | "")}
            aria-label="Filter by outcome"
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
          rowKey={(call) => call.id}
          loading={list.loading}
          onRowClick={(call) => setSelectedId(call.id)}
          empty={
            <EmptyState
              title="No calls yet"
              description={
                search || outcome
                  ? "No calls match these filters."
                  : "Calls will appear here as soon as the agent answers its first one."
              }
            />
          }
        />

        {list.meta && <Pagination meta={list.meta} onPageChange={list.setPage} />}
      </Card>

      <CallDetailModal callId={selectedId} onClose={() => setSelectedId(null)} />
    </>
  );
}

function CallDetailModal({
  callId,
  onClose,
}: {
  callId: string | null;
  onClose: () => void;
}) {
  // Fetches only while a call is selected; the detail endpoint carries the
  // transcript, which the list deliberately omits.
  const detail = useApiQuery(
    (signal) => (callId ? callApi.get(callId, signal) : Promise.resolve(null)),
    [callId],
  );

  const call = detail.data;

  return (
    <Modal
      open={Boolean(callId)}
      onClose={onClose}
      title="Call detail"
      description={call ? formatDateTime(call.started_at ?? call.created_at) : undefined}
      size="lg"
    >
      {detail.loading && <p className="text-sm text-ink-subtle">Loading transcript…</p>}
      {detail.error && <Alert tone="danger">{detail.error.message}</Alert>}

      {call && (
        <div className="flex flex-col gap-5">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <Detail label="Caller" value={formatPhone(call.caller_number)} />
            <Detail label="Customer" value={call.customer?.name || "Not identified"} />
            <Detail label="Length" value={formatDuration(call.duration_seconds)} />
            <Detail label="Cost" value={formatPaise(call.cost_paise)} />
          </dl>

          <div className="flex flex-wrap gap-2">
            <CallOutcomeBadge outcome={call.outcome} />
            <LanguageBadge language={call.language} />
          </div>

          {call.summary && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-1.5">
                Summary
              </h3>
              <p className="text-sm text-ink-muted">{call.summary}</p>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-1.5">
              Transcript
            </h3>
            {call.transcript ? (
              // `whitespace-pre-wrap` preserves the speaker-per-line structure
              // without the horizontal scroll a <pre> would introduce.
              <p className="text-sm text-ink whitespace-pre-wrap bg-surface-sunken rounded-lg p-4 max-h-80 overflow-y-auto">
                {call.transcript}
              </p>
            ) : (
              <p className="text-sm text-ink-subtle">No transcript was captured.</p>
            )}
          </section>

          {call.recording_url && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle mb-1.5">
                Recording
              </h3>
              <CallRecording callId={call.id} />
            </section>
          )}
        </div>
      )}
    </Modal>
  );
}

/**
 * The recording, fetched when someone actually opens the call.
 *
 * The URL stored on the call is VAPI's private object: it requires
 * authorization and returns 400 to a browser, so the player sat there looking
 * broken. The playable form is presigned and expires within the hour, which is
 * why it cannot be stored and is asked for on open instead.
 */
function CallRecording({ callId }: { callId: string }) {
  const [url, setUrl] = useState("");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let cancelled = false;
    callApi
      .recording(callId)
      .then((result) => {
        if (cancelled) return;
        if (result.url) setUrl(result.url);
        else setProblem(result.reason || "No recording is available.");
      })
      .catch(() => {
        if (!cancelled) setProblem("The recording could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
  }, [callId]);

  if (problem) return <p className="text-sm text-ink-subtle">{problem}</p>;
  if (!url) return <p className="text-sm text-ink-subtle">Loading the recording…</p>;

  return (
    <audio controls src={url} className="w-full">
      Your browser cannot play this recording.
    </audio>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wide text-ink-subtle">{label}</dt>
      <dd className="text-sm text-ink mt-0.5">{value}</dd>
    </div>
  );
}
