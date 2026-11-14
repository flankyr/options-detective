import { useEffect, useState } from "react";
import { api, type ArchiveStatus } from "../../lib/api";
import { friendlyError } from "../../lib/friendlyError";
import { useStore } from "../../store";
import Button from "../ui/Button";
import { FormInput } from "../ui/Input";
import { ProvidersSection } from "./SettingsProvidersSection";

// Phase 0 (roadmap v2): the chain archive. One parquet snapshot per symbol per
// trading day, taken after the 16:00 ET close. This is the raw material for
// every backtest and calibration plot in Phases 3-5, so the UI's job is to
// make "is it running, what does it cover" visible and give a manual trigger.

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function DataTab() {
  const showToast = useStore((s) => s.showToast);
  const [status, setStatus] = useState<ArchiveStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [extra, setExtra] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const s = await api.archiveStatus();
      setStatus(s);
      setExtra(s.settings.extraSymbols.join(", "));
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function saveSettings(patch: Partial<ArchiveStatus["settings"]>) {
    try {
      await api.archiveSettings(patch);
      await refresh();
    } catch (err) {
      showToast(friendlyError(err));
    }
  }

  async function snapshotNow(force: boolean) {
    setBusy(true);
    try {
      const r = await api.archiveSnapshot({ force });
      if (r.ok) {
        const n = r.written?.length ?? 0;
        const bad = Object.keys(r.errors ?? {}).length;
        showToast(bad ? `Archived ${n}, ${bad} failed` : `✓ Archived ${n} symbol${n === 1 ? "" : "s"} for ${r.date}`);
      } else {
        showToast(`Archive failed: ${r.error ?? "unknown error"}`);
      }
      await refresh();
    } catch (err) {
      showToast(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  const symbols = status ? Object.entries(status.symbols).sort(([a], [b]) => a.localeCompare(b)) : [];
  const totalBytes = symbols.reduce((acc, [, v]) => acc + v.bytes, 0);
  const totalDays = symbols.reduce((acc, [, v]) => Math.max(acc, v.days), 0);

  return (
    <div className="animate-card-enter" data-testid="data-settings">
      <ProvidersSection />
      <div className="mb-3 rounded-md bg-dark-700/50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-medium">Option chain archive</div>
            <div className="mt-0.5 text-xs text-content-3">
              Saves every watched symbol's full chain once per trading day after the close.
              Backtests and calibration (coming in v2.0) read these files; every day not
              recorded is history that cannot be bought back for free.
            </div>
          </div>
          <Button variant="secondary" size="sm" disabled={busy || !status}
            data-testid="archive-snapshot-now" onClick={() => snapshotNow(false)}>
            {busy ? "Snapshotting…" : "Snapshot now"}
          </Button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm text-content-2">
          <input type="checkbox" checked={status?.settings.enabled ?? true}
            disabled={!status}
            onChange={(e) => saveSettings({ enabled: e.target.checked })}
            data-testid="archive-enabled" className="accent-accent-primary" />
          Snapshot automatically (checked hourly while the app runs)
        </label>
        {error && <div className="mt-2 text-xs text-accent-red">{error}</div>}
        {status?.last && (
          <div className="mt-2 text-xs text-content-3" data-testid="archive-last">
            Last run {(status.last.at ?? "").slice(0, 16).replace("T", " ")} UTC:{" "}
            {status.last.ok
              ? `${status.last.written?.length ?? 0} written, ${status.last.skipped?.length ?? 0} already current`
              : `failed (${status.last.error})`}
          </div>
        )}
      </div>

      <div className="mb-3 rounded-md bg-dark-700/50 p-4">
        <div className="text-sm font-medium">Extra symbols</div>
        <div className="mt-0.5 text-xs text-content-3">
          SPY, QQQ, IWM, AAPL, MSFT, NVDA, TSLA and AMZN are always archived, plus every
          symbol with an open journal trade. Add more here, comma separated.
        </div>
        <div className="mt-2 flex items-end gap-2">
          <FormInput label="Symbols" value={extra} placeholder="VGT, XLE, GLD"
            onChange={(e) => setExtra(e.target.value)} data-testid="archive-extra" />
          <Button variant="secondary" size="sm" disabled={!status}
            onClick={() => saveSettings({ extraSymbols: extra.split(/[,\s]+/).filter(Boolean) })}>
            Save
          </Button>
        </div>
      </div>

      <div className="rounded-md bg-dark-700/50 p-4">
        <div className="flex items-baseline justify-between">
          <div className="text-sm font-medium">Coverage</div>
          <div className="text-xs text-content-3">
            {status ? `${totalDays} day${totalDays === 1 ? "" : "s"} · ${fmtBytes(totalBytes)} · ${status.root}` : "Loading…"}
          </div>
        </div>
        {status && status.missingToday.length > 0 && (
          <div className="mt-1 text-xs text-accent-orange" data-testid="archive-missing">
            Missing for {status.tradingDate}: {status.missingToday.join(", ")}
          </div>
        )}
        {symbols.length > 0 && (
          <table className="mt-2 w-full text-xs">
            <thead className="text-content-3">
              <tr><th className="text-left font-normal">Symbol</th><th className="text-right font-normal">Days</th>
                <th className="text-right font-normal">First</th><th className="text-right font-normal">Last</th></tr>
            </thead>
            <tbody className="font-mono text-content-2">
              {symbols.map(([sym, v]) => (
                <tr key={sym}>
                  <td>{sym}</td><td className="text-right">{v.days}</td>
                  <td className="text-right">{v.first ?? "—"}</td><td className="text-right">{v.last ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
