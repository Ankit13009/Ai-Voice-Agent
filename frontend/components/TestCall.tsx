"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { businessApi } from "@/lib/api/endpoints";
import type { TestCallConfig } from "@/types/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  cn,
} from "@/components/ui";

/**
 * Browser test call.
 *
 * Talks to this business's own VAPI assistant over WebRTC from the browser, so
 * the agent can be exercised without buying a phone number. Two uses: checking
 * the agent behaves before spending money on telephony, and demoing it live to
 * a prospect who does not have to dial anything.
 *
 * Honest caveat, surfaced in the UI as well: browser audio is clean wideband,
 * while a real call is 8 kHz narrowband over the PSTN. Speech recognition,
 * especially for Hindi, performs measurably worse on a real line, and this test
 * skips the telephony hop entirely, so latency here is optimistic.
 *
 * The SDK is imported dynamically because it touches `window` and `navigator`
 * at module scope, which breaks Next's server render.
 */

type CallState = "idle" | "connecting" | "active" | "ending";

interface TranscriptLine {
  id: number;
  role: "user" | "assistant";
  text: string;
}

export function TestCall() {
  const [config, setConfig] = useState<TestCallConfig | null>(null);
  const [configError, setConfigError] = useState<ApiError | null>(null);
  const [state, setState] = useState<CallState>("idle");
  const [error, setError] = useState<string>("");
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [seconds, setSeconds] = useState(0);

  // The SDK instance is deliberately not React state: recreating it on render
  // would drop the live call.
  const vapiRef = useRef<any>(null);
  const lineId = useRef(0);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    businessApi
      .testCallConfig()
      .then((result) => active && setConfig(result))
      .catch((err) => {
        if (!active) return;
        setConfigError(err instanceof ApiError ? err : null);
      });
    return () => {
      active = false;
    };
  }, []);

  // Call duration, which is also the cheapest signal that the call is alive.
  useEffect(() => {
    if (state !== "active") return;
    const timer = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [state]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  // Hang up if the user navigates away mid-call, otherwise the call keeps
  // running (and billing) with nothing on screen.
  useEffect(() => {
    return () => {
      try {
        vapiRef.current?.stop();
      } catch {
        /* the SDK throws if there is no active call; nothing to do */
      }
    };
  }, []);

  const appendLine = useCallback((role: "user" | "assistant", text: string) => {
    setTranscript((current) => {
      const last = current[current.length - 1];
      // Merge consecutive lines from the same speaker so partial transcripts
      // do not produce one row per word.
      if (last && last.role === role) {
        return [...current.slice(0, -1), { ...last, text }];
      }
      return [...current, { id: lineId.current++, role, text }];
    });
  }, []);

  const startCall = useCallback(async () => {
    if (!config) return;
    setError("");
    setTranscript([]);
    setSeconds(0);
    setState("connecting");

    try {
      const { default: Vapi } = await import("@vapi-ai/web");
      const vapi = new Vapi(config.public_key);
      vapiRef.current = vapi;

      vapi.on("call-start", () => setState("active"));
      vapi.on("call-end", () => {
        setState("idle");
        setAssistantSpeaking(false);
      });
      vapi.on("speech-start", () => setAssistantSpeaking(true));
      vapi.on("speech-end", () => setAssistantSpeaking(false));

      vapi.on("message", (message: any) => {
        if (message?.type !== "transcript") return;
        const role = message.role === "assistant" ? "assistant" : "user";
        if (typeof message.transcript === "string" && message.transcript.trim()) {
          appendLine(role, message.transcript);
        }
      });

      vapi.on("error", (err: any) => {
        // The SDK's error shapes vary; keep whatever is human-readable.
        const detail =
          err?.errorMsg || err?.error?.message || err?.message || "The call failed.";
        setError(String(detail));
        setState("idle");
      });

      await vapi.start(config.assistant_id);
    } catch (err) {
      setState("idle");
      // A refused microphone is by far the most common failure, and the raw
      // browser error ("Permission denied") does not tell anyone what to do.
      const name = (err as Error)?.name ?? "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        setError(
          "Microphone access was blocked. Allow the microphone for this site in your browser's address bar, then try again.",
        );
      } else if (name === "NotFoundError") {
        setError("No microphone was found. Plug one in and try again.");
      } else {
        setError((err as Error)?.message || "Could not start the call.");
      }
    }
  }, [config, appendLine]);

  const endCall = useCallback(() => {
    setState("ending");
    try {
      vapiRef.current?.stop();
    } catch {
      /* already stopped */
    }
    setState("idle");
  }, []);

  const toggleMute = useCallback(() => {
    const next = !muted;
    setMuted(next);
    try {
      vapiRef.current?.setMuted(next);
    } catch {
      /* not in a call */
    }
  }, [muted]);

  // --- Not configured yet: explain what is missing rather than showing a dead button ---
  if (configError) {
    return (
      <Card>
        <CardHeader
          title="Test call"
          description="Talk to your agent from the browser, before buying a phone number."
        />
        <CardBody>
          <Alert tone="warning" title="Not ready yet">
            {configError.message}
          </Alert>
        </CardBody>
      </Card>
    );
  }

  const duration = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  const busy = state === "connecting" || state === "ending";

  return (
    <Card>
      <CardHeader
        title="Test call"
        description={
          config
            ? `Speak to ${config.agent_name} the way a customer would.`
            : "Loading…"
        }
        action={
          state === "active" ? (
            <div className="flex items-center gap-2">
              <Badge tone="success" dot>
                Live {duration}
              </Badge>
            </div>
          ) : null
        }
      />

      <CardBody className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}

        <div className="flex items-center gap-3">
          {state === "idle" ? (
            <Button
              variant="primary"
              size="lg"
              onClick={startCall}
              disabled={!config}
              leadingIcon={
                <svg
                  className="h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  aria-hidden="true"
                >
                  <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.1a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z" />
                </svg>
              }
            >
              Start test call
            </Button>
          ) : (
            <>
              <Button variant="danger" size="lg" onClick={endCall} loading={busy}>
                End call
              </Button>
              <Button variant="secondary" size="lg" onClick={toggleMute}>
                {muted ? "Unmute" : "Mute"}
              </Button>
            </>
          )}

          {state === "connecting" && (
            <span className="text-sm text-ink-subtle">Connecting…</span>
          )}
          {state === "active" && (
            <span
              className={cn(
                "text-sm transition-colors",
                assistantSpeaking ? "text-primary font-medium" : "text-ink-subtle",
              )}
            >
              {assistantSpeaking ? `${config?.agent_name} is speaking…` : "Listening…"}
            </span>
          )}
        </div>

        {transcript.length > 0 && (
          <div className="max-h-72 overflow-y-auto rounded-lg bg-surface-sunken p-4 flex flex-col gap-3">
            {transcript.map((line) => (
              <div key={line.id}>
                <p className="text-2xs uppercase tracking-wide text-ink-subtle mb-0.5">
                  {line.role === "assistant" ? config?.agent_name ?? "Agent" : "You"}
                </p>
                <p className="text-sm text-ink">{line.text}</p>
              </div>
            ))}
            <div ref={transcriptEndRef} />
          </div>
        )}

        <p className="text-xs text-ink-subtle">
          This is a browser call, so it sounds better than a real one. A phone line is
          narrowband and adds network latency, which makes Hindi recognition harder. Use
          this to check the agent behaves correctly, then confirm quality on a real call
          once a number is connected.
        </p>
      </CardBody>
    </Card>
  );
}
