"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import clsx from "clsx";
import { KeyRound, ShieldAlert, ShieldCheck, TimerReset } from "lucide-react";
import { Card } from "@/components/Card";
import { LoadingBlock } from "@/components/Spinner";
import { KeyState, type AdminKeyRead } from "@/lib/types";

/** Ticks once a second so cooldown countdowns stay live. */
function useNowSeconds(): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function fmtCountdown(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function StatePill({
  k,
  now,
}: {
  k: AdminKeyRead;
  now: number;
}): JSX.Element {
  if (k.state === KeyState.HEALTHY) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-ok-500/40 bg-ok-500/10 px-2.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-ok-400">
        <ShieldCheck className="h-3 w-3" />
        healthy
      </span>
    );
  }
  if (k.state === KeyState.COOLDOWN) {
    const remaining = k.available_at != null ? k.available_at - now : null;
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-warn-500/40 bg-warn-500/10 px-2.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-warn-400">
        <TimerReset className="h-3 w-3" />
        cooldown
        {remaining != null && remaining > 0 && (
          <span className="tabular-nums normal-case">
            · {fmtCountdown(remaining)}
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-danger-500/40 bg-danger-500/10 px-2.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-danger-400">
      <ShieldAlert className="h-3 w-3" />
      disabled
    </span>
  );
}

export default function AdminPage(): JSX.Element {
  const now = useNowSeconds();
  const { data, error, isLoading } = useSWR<AdminKeyRead[]>("/admin/keys", {
    refreshInterval: 10_000,
  });

  const keys = data ?? [];
  const healthy = keys.filter((k) => k.state === KeyState.HEALTHY).length;

  return (
    <div className="flex animate-fade-up flex-col gap-6">
      <header>
        <div className="kicker">Operations</div>
        <h1 className="page-title">API Keys</h1>
        <p className="page-sub">
          Sarvam STT key pool health — refreshed every 10 seconds.
        </p>
      </header>

      {isLoading && <LoadingBlock label="Loading key pool..." />}
      {error && (
        <Card>
          <p className="text-sm text-danger-400">
            Failed to load /admin/keys: {String(error.message ?? error)}
          </p>
        </Card>
      )}

      {!isLoading && !error && (
        <Card
          padded={false}
          title="Key pool"
          right={
            <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400">
              <KeyRound className="h-3.5 w-3.5 text-brand-500" />
              {healthy}/{keys.length} healthy
            </span>
          }
        >
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>State</th>
                  <th className="text-right">OK</th>
                  <th className="text-right">Errors</th>
                  <th className="text-right">Error rate</th>
                </tr>
              </thead>
              <tbody>
                {keys.length === 0 && (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-3 py-8 text-center text-ink-400"
                    >
                      No keys configured.
                    </td>
                  </tr>
                )}
                {keys.map((k, i) => {
                  const total = k.ok_count + k.err_count;
                  const errRate = total > 0 ? k.err_count / total : 0;
                  return (
                    <tr key={`${k.masked}-${i}`}>
                      <td className="font-mono text-xs text-ink-800">
                        {k.masked}
                      </td>
                      <td>
                        <StatePill k={k} now={now} />
                      </td>
                      <td className="text-right font-mono text-xs tabular-nums text-ok-400">
                        {k.ok_count.toLocaleString()}
                      </td>
                      <td
                        className={clsx(
                          "text-right font-mono text-xs tabular-nums",
                          k.err_count > 0 ? "text-danger-400" : "text-ink-400",
                        )}
                      >
                        {k.err_count.toLocaleString()}
                      </td>
                      <td className="text-right font-mono text-xs tabular-nums text-ink-500">
                        {total > 0 ? `${(errRate * 100).toFixed(1)}%` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
