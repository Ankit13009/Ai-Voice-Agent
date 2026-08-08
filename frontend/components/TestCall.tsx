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
  Field,
  Select,
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
  // Whether the agent has ever transcribed the caller. A silent transcriber is
  // the failure that looks most like "the bot is broken", so it gets its own
  // warning rather than leaving you talking to something that cannot hear.
  const heardUser = useRef(false);
  const [micWarning, setMicWarning] = useState(false);

  // Independent microphone check. When the agent hears nothing, the question is
  // whether the browser is capturing audio at all or whether it is capturing it
  // and the call is not carrying it. A level meter answers that in two seconds,
  // where guessing at permissions can take an hour.
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  // The microphone level *as VAPI sees it*. The standalone meter below proves
  // the browser can hear you; this proves the call can. They can disagree, which
  // is exactly the failure where the SDK picked a silent virtual input device.
  const [inCallLevel, setInCallLevel] = useState(0);
  const [micLevel, setMicLevel] = useState<number | null>(null);
  const [micError, setMicError] = useState("");
  // Captured on failure. "I allowed it" usually means the site prompt was
  // allowed, which is only one of three gates: the page must also be a secure
  // context, and the browser itself needs OS-level microphone permission.
  const [micDiagnostics, setMicDiagnostics] = useState<string[]>([]);
  const micStopRef = useRef<(() => void) | null>(null);

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

  // Surface a transcriber that is not hearing anything, instead of leaving you
  // to conclude the agent is broken.
  useEffect(() => {
    if (state !== "active") return;
    const check = window.setTimeout(() => {
      if (!heardUser.current) setMicWarning(true);
    }, 15000);
    return () => window.clearTimeout(check);
  }, [state, seconds === 0]);

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

  // Device labels are only readable once microphone permission is granted, so
  // this is refreshed after any successful getUserMedia.
  const refreshDevices = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "audioinput" && d.deviceId));
    } catch {
      /* nothing we can do; the picker just stays empty */
    }
  }, []);

  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);

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

  /**
   * Force the local microphone on, retrying while the track comes up.
   *
   * Goes through Daily's `setLocalAudio` as well as the SDK wrapper: the
   * wrapper's setMuted is a no-op if the local participant is not ready, while
   * Daily's call object accepts it and applies it once the track publishes.
   */
  const ensureUnmuted = useCallback(async (vapi: any) => {
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        vapi.setMuted(false);
        const daily = vapi.getDailyCallObject?.();
        if (daily) {
          daily.setLocalAudio(true);
          if (daily.localAudio()) {
            setMuted(false);
            setError((e) => (e.includes("started muted") ? "" : e));
            return true;
          }
        } else if (!vapi.isMuted()) {
          setMuted(false);
          return true;
        }
      } catch {
        /* the call may not be ready yet; retry below */
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    setError(
      "The call is connected but the microphone stayed muted. Try selecting the microphone explicitly above, or test from the VAPI dashboard to confirm the agent itself is fine.",
    );
    return false;
  }, []);

  const startCall = useCallback(async () => {
    if (!config) return;
    setError("");
    setTranscript([]);
    setSeconds(0);
    setMicWarning(false);
    setInCallLevel(0);
    heardUser.current = false;

    // Release the diagnostic microphone stream first. Holding it open while the
    // call tries to acquire the same device is its own source of failure.
    micStopRef.current?.();
    micStopRef.current = null;
    setMicLevel(null);

    setState("connecting");

    try {
      const { default: Vapi } = await import("@vapi-ai/web");

      // Configure the audio source at construction rather than after the call
      // starts. Setting it afterwards means the peer connection is negotiated
      // with whatever the SDK picked by default, and on this machine that can be
      // a track that carries no audio. `startAudioOff: false` is explicit for
      // the same reason: a call that begins with the mic disabled looks
      // identical to a broken microphone from the outside.
      const vapi = new Vapi(
        config.public_key,
        undefined,
        { alwaysIncludeMicInPermissionPrompt: true },
        { audioSource: deviceId || true, startAudioOff: false },
      );
      vapiRef.current = vapi;

      // Treat any sign of life as "connected". `call-start` is the intended
      // signal but is not always delivered; the bot speaking is proof enough.
      const markActive = () => setState((s) => (s === "connecting" ? "active" : s));

      // Unmute on call-start, not when start() resolves. start() returns as soon
      // as the call object exists, which is before the local audio track is
      // published, so an unmute issued there silently does nothing.
      vapi.on("call-start", () => {
        markActive();
        void ensureUnmuted(vapi);
      });
      vapi.on("call-end", () => {
        setState("idle");
        setAssistantSpeaking(false);
      });
      vapi.on("speech-start", () => {
        markActive();
        setAssistantSpeaking(true);
      });
      vapi.on("speech-end", () => setAssistantSpeaking(false));

      vapi.on("message", (message: any) => {
        if (message?.type !== "transcript") return;
        markActive();
        const role = message.role === "assistant" ? "assistant" : "user";
        if (typeof message.transcript === "string" && message.transcript.trim()) {
          if (role === "user") heardUser.current = true;
          appendLine(role, message.transcript);
        }
      });

      // Live microphone level from inside the call. If this stays at zero while
      // the standalone meter moves, the call is listening to the wrong device.
      vapi.on("local-volume-level", (volume: number) => {
        setInCallLevel(Math.min(100, Math.round(volume * 100 * 3)));
        if (volume > 0.02) heardUser.current = true;
      });

      // Connection failures are otherwise invisible: the greeting can play while
      // the upstream leg never establishes.
      vapi.on("call-start-failed", (e: any) => {
        setError(`The call failed to connect at stage "${e?.stage}": ${e?.error ?? "unknown"}`);
        setState("idle");
      });

      vapi.on("error", (err: any) => {
        // The SDK's error shapes vary; keep whatever is human-readable.
        const detail =
          err?.errorMsg || err?.error?.message || err?.message || "The call failed.";
        setError(String(detail));
        setState("idle");
      });

      await vapi.start(config.assistant_id);

      // Pin the microphone explicitly. Left to itself the SDK takes the browser
      // default, which on this machine can be a virtual device that only ever
      // produces silence.
      if (deviceId) {
        try {
          await vapi.setInputDevicesAsync({ audioDeviceId: deviceId });
        } catch {
          /* fall back to the default device */
        }
      }
      void ensureUnmuted(vapi);

      try {
        await vapi.startLocalAudioLevelObserver(100);
      } catch {
        /* the level meter is diagnostic only; the call still works without it */
      }
      void refreshDevices();
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
  }, [config, appendLine, deviceId, refreshDevices, ensureUnmuted]);

  const endCall = useCallback(() => {
    // Force idle unconditionally. The SDK's stop() can throw or resolve late,
    // and a hang-up button that keeps spinning leaves no way out of the call.
    try {
      vapiRef.current?.stop();
    } catch {
      /* already stopped */
    }
    setState("idle");
    setAssistantSpeaking(false);
  }, []);

  const checkMicrophone = useCallback(async () => {
    setMicError("");
    if (micStopRef.current) {
      micStopRef.current();
      micStopRef.current = null;
      setMicLevel(null);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);

      let raf = 0;
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        // Peak deviation from silence (128), scaled to 0-100.
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
        setMicLevel(Math.min(100, Math.round((peak / 128) * 100 * 2.5)));
        raf = requestAnimationFrame(tick);
      };
      tick();

      void refreshDevices();

      micStopRef.current = () => {
        cancelAnimationFrame(raf);
        stream.getTracks().forEach((track) => track.stop());
        void context.close();
      };
    } catch (err) {
      const name = (err as Error)?.name ?? "";

      // Work out which gate actually failed rather than listing all of them.
      const lines: string[] = [];
      lines.push(`Page origin: ${window.location.origin}`);
      lines.push(
        window.isSecureContext
          ? "Secure context: yes"
          : "Secure context: NO — browsers refuse the microphone on plain http unless the host is localhost or 127.0.0.1",
      );

      try {
        const status = await navigator.permissions?.query({
          name: "microphone" as PermissionName,
        });
        if (status) lines.push(`Site permission: ${status.state}`);
      } catch {
        lines.push("Site permission: could not be read in this browser");
      }

      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const inputs = devices.filter((d) => d.kind === "audioinput");
        lines.push(`Microphones visible: ${inputs.length}`);
        // Labels stay blank until permission is actually granted, which is a
        // reliable tell that the block is above the page level.
        const named = inputs.filter((d) => d.label).map((d) => d.label);
        lines.push(
          named.length
            ? `Devices: ${named.join(", ")}`
            : "Device names hidden — the browser has not granted access, so the block is at browser or OS level, not the site prompt",
        );
      } catch {
        lines.push("Could not list audio devices");
      }

      setMicDiagnostics(lines);
      setMicError(
        name === "NotAllowedError"
          ? "The microphone was blocked. If you already allowed it for this site, the block is one level up: either the browser lacks microphone permission in macOS System Settings, or this page is not a secure context. The details below say which."
          : name === "NotFoundError"
            ? "No microphone was found."
            : (err as Error)?.message || "Could not open the microphone.",
      );
    }
  }, [refreshDevices]);

  useEffect(() => () => micStopRef.current?.(), []);

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

        {micWarning && !heardUser.current && (
          <Alert tone="warning" title="Nothing heard from you yet">
            The agent is speaking but nothing you say is being transcribed. Check the
            microphone is not muted, that the browser is using the right input device,
            and that this tab has microphone permission.
          </Alert>
        )}

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

        {state === "active" && (
          <div className="flex items-center gap-3">
            <span className="text-xs text-ink-subtle w-28 shrink-0">Your mic, in call</span>
            <div className="h-2 flex-1 rounded-full bg-surface-sunken overflow-hidden">
              <div
                className={cn(
                  "h-full transition-[width] duration-75",
                  inCallLevel > 4 ? "bg-success" : "bg-line-strong",
                )}
                style={{ width: `${inCallLevel}%` }}
              />
            </div>
            <span className="text-xs tnum text-ink-subtle w-24 shrink-0">
              {inCallLevel > 4 ? "sending audio" : "silent"}
            </span>
          </div>
        )}

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

        <div className="rounded-lg border border-line p-3 flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-ink">Microphone check</p>
              <p className="text-xs text-ink-subtle">
                Speak and watch the bar. No movement means the browser is not hearing you.
              </p>
            </div>
            <Button variant="secondary" size="sm" onClick={checkMicrophone}>
              {micLevel === null ? "Check microphone" : "Stop"}
            </Button>
          </div>

          {devices.length > 0 && (
            <Field
              label="Microphone to use"
              hint={
                devices.length > 1
                  ? "Pick your real microphone. Virtual devices from meeting apps capture silence."
                  : undefined
              }
            >
              <Select
                value={deviceId}
                onChange={(e) => setDeviceId(e.target.value)}
                placeholder="Browser default"
                options={devices.map((d, i) => ({
                  value: d.deviceId,
                  label: d.label || `Microphone ${i + 1}`,
                }))}
              />
            </Field>
          )}
          {micError && (
            <Alert tone="danger" title="Microphone blocked">
              <p>{micError}</p>
              {micDiagnostics.length > 0 && (
                <ul className="mt-2 flex flex-col gap-0.5 font-mono text-2xs opacity-90">
                  {micDiagnostics.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </Alert>
          )}
          {micLevel !== null && (
            <div className="flex items-center gap-3">
              <div className="h-2 flex-1 rounded-full bg-surface-sunken overflow-hidden">
                <div
                  className={cn(
                    "h-full transition-[width] duration-75",
                    micLevel > 6 ? "bg-success" : "bg-line-strong",
                  )}
                  style={{ width: `${micLevel}%` }}
                />
              </div>
              <span className="text-xs tnum text-ink-subtle w-20 shrink-0">
                {micLevel > 6 ? "hearing you" : "silent"}
              </span>
            </div>
          )}
        </div>

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
