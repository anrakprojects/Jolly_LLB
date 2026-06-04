import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { StatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

/** Gateway + session summary for the System sidebar block (no separate strip chrome). */
export function SidebarStatusStrip({ status, onRefresh }: SidebarStatusStripProps) {
  const { t } = useI18n();

  if (status === null) {
    return (
      <div className="px-5 py-1.5" aria-hidden>
        <div className="h-2 w-[80%] max-w-full animate-pulse rounded-sm bg-midground/10" />
      </div>
    );
  }

  const gw = gatewayLine(status, t);
  const { activeSessionsLabel, gatewayStatusLabel } = t.app;
  const isRunning =
    status.gateway_running || status.gateway_state === "running";

  const linkClass = cn(
    "break-words text-left transition-colors hover:text-midground",
    "focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
  );

  return (
    <div className="px-5 pb-2 pt-0.5 text-text-secondary">
      <div className="flex flex-col gap-1 font-mondwest text-xs leading-snug tracking-[0.08em]">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Link to="/sessions" title={t.app.statusOverview} className={linkClass}>
            <span className="text-text-tertiary">{gatewayStatusLabel}</span>{" "}
            <span className={cn("inline-flex items-center gap-1 font-medium", gw.tone)}>
              {/* Pulsing green "live" dot — only once the gateway is truly up. */}
              {isRunning && <LiveDot />}
              {gw.label}
            </span>
          </Link>

          <GatewayToggle status={status} onRefresh={onRefresh} />
        </div>

        <Link
          to="/sessions"
          title={t.app.statusOverview}
          className={cn("block", linkClass)}
        >
          <span className="text-text-tertiary">{activeSessionsLabel}</span>{" "}
          <span className="tabular-nums text-text-secondary">
            {status.active_sessions}
          </span>
        </Link>
      </div>
    </div>
  );
}

/** Pulsing green "live" dot shown beside the status label when the gateway is up. */
function LiveDot() {
  return (
    <span className="relative flex size-1.5" aria-hidden>
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
      <span className="relative inline-flex size-1.5 rounded-full bg-success" />
    </span>
  );
}

/**
 * Switch that brings the gateway up/down straight from the dashboard sidebar.
 *
 * Visual states:
 *  - off       → muted track, knob parked left.
 *  - starting  → warning-toned track with a spinning loader in the knob; held
 *                until /api/status reports the gateway running (or a ~40s cap).
 *  - live      → green track, knob slid right (paired with the LiveDot above).
 *
 * Backend: POST /api/gateway/{start,stop} spawn the real `hermes gateway`
 * verbs, so the toggle behaves exactly like the CLI. The server keeps
 * reporting "stopped" until the spawned process registers a PID, so we hold an
 * optimistic `pending` state to keep the spinner up across that gap rather
 * than letting the toggle snap back to "off" mid-start.
 */
function GatewayToggle({
  status,
  onRefresh,
}: {
  status: StatusResponse;
  onRefresh?: () => void;
}) {
  const { t } = useI18n();
  const g = t.app.gatewayStrip;
  const [pending, setPending] = useState<null | "starting" | "stopping">(null);

  const serverRunning =
    status.gateway_running || status.gateway_state === "running";

  // Drop the optimistic state once the server confirms the transition landed.
  // Reconciling during render (guarded so it can't loop) is React's recommended
  // alternative to a state-syncing effect:
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  if (
    (pending === "starting" && serverRunning) ||
    (pending === "stopping" && !serverRunning)
  ) {
    setPending(null);
  }

  // While a transition is pending, nudge the parent poll so the toggle settles
  // promptly instead of waiting for the slow 10s sidebar tick. Cap at ~40s so a
  // start/stop that never lands falls back to the real server state.
  useEffect(() => {
    if (!pending || !onRefresh) return;
    let ticks = 0;
    const id = setInterval(() => {
      ticks += 1;
      onRefresh();
      if (ticks >= 16) {
        clearInterval(id);
        setPending(null);
      }
    }, 2500);
    return () => clearInterval(id);
  }, [pending, onRefresh]);

  const busy = pending !== null || status.gateway_state === "starting";
  const on =
    pending === "starting"
      ? true
      : pending === "stopping"
        ? false
        : serverRunning;

  const handleClick = async () => {
    if (busy) return;
    const next = on ? "stopping" : "starting";
    setPending(next);
    try {
      if (next === "starting") await api.startGateway();
      else await api.stopGateway();
    } catch {
      // Spawn failed — drop back to the real server state immediately.
      setPending(null);
    }
    onRefresh?.();
  };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-busy={busy}
      aria-label={on ? g.turnOff : g.turnOn}
      title={busy ? g.starting : on ? g.turnOff : g.turnOn}
      onClick={handleClick}
      disabled={busy}
      className={cn(
        "relative inline-flex h-4 w-7 shrink-0 items-center rounded-full border",
        "transition-colors focus-visible:outline-none focus-visible:ring-1",
        busy
          ? "cursor-progress border-warning/50 bg-warning/25 focus-visible:ring-warning/40"
          : on
            ? "cursor-pointer border-success/50 bg-success/80 focus-visible:ring-success/40"
            : "cursor-pointer border-midground/30 bg-midground/15 hover:bg-midground/25 focus-visible:ring-midground/40",
      )}
    >
      <span
        className={cn(
          "pointer-events-none flex size-3 items-center justify-center rounded-full bg-white shadow-sm",
          "transition-transform duration-200 ease-out",
          on ? "translate-x-3" : "translate-x-0.5",
        )}
      >
        {/* The "loading round": a spinner held in the knob until the gateway
            is up (or fully down). */}
        {busy && (
          <Loader2 className="size-2.5 animate-spin text-warning" aria-hidden />
        )}
      </span>
    </button>
  );
}

export function gatewayLine(
  status: StatusResponse,
  t: ReturnType<typeof useI18n>["t"],
): { label: string; tone: string } {
  const g = t.app.gatewayStrip;
  const byState: Record<string, { label: string; tone: string }> = {
    running: { label: g.running, tone: "text-success" },
    starting: { label: g.starting, tone: "text-warning" },
    startup_failed: { label: g.failed, tone: "text-destructive" },
    stopped: { label: g.stopped, tone: "text-muted-foreground" },
  };
  if (status.gateway_state && byState[status.gateway_state]) {
    return byState[status.gateway_state];
  }
  return status.gateway_running
    ? { label: g.running, tone: "text-success" }
    : { label: g.off, tone: "text-muted-foreground" };
}

interface SidebarStatusStripProps {
  status: StatusResponse | null;
  onRefresh?: () => void;
}
