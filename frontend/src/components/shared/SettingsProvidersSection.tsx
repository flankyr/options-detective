import { useEffect, useState } from "react";
import { api, type DataProvidersStatus } from "../../lib/api";
import { friendlyError } from "../../lib/friendlyError";
import { useStore } from "../../store";
import Button from "../ui/Button";
import { FormInput } from "../ui/Input";

// Phase 1 (roadmap v2): which sources feed the app, in which order, and the
// risk-free curve. Live chains try Massive first when a key is set, then
// yfinance; historical dates use the local archive, then DoltHub.

const LABELS: Record<string, string> = {
  massive: "Massive (formerly Polygon) — 15-min delayed chain with greeks and OI; free key",
  yfinance: "Yahoo Finance via yfinance — unofficial, last resort",
  archive: "Local archive — your own daily snapshots",
  dolthub: "DoltHub options dataset — end-of-day history with greeks, no volume/OI",
};

export function ProvidersSection() {
  const showToast = useStore((s) => s.showToast);
  const [status, setStatus] = useState<DataProvidersStatus | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setStatus(await api.dataProviders());
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }
  useEffect(() => { void refresh(); }, []);

  async function saveKey(value: string) {
    setBusy(true);
    try {
      setStatus(await api.dataProvidersUpdate({ massiveApiKey: value }));
      setKey("");
      showToast(value ? "✓ Massive key saved" : "Massive key removed");
    } catch (err) {
      showToast(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  const massiveSet = status?.config?.massiveApiKey === "set";
  const rates = status?.rates;

  return (
    <>
      <div className="mb-3 rounded-md bg-dark-700/50 p-4" data-testid="providers-settings">
        <div className="text-sm font-medium">Market data sources</div>
        <div className="mt-0.5 text-xs text-content-3">
          Every chain is stamped with the source that produced it. Live screens use the first
          configured live source; backtests read the archive, then DoltHub.
        </div>
        {error && <div className="mt-2 text-xs text-accent-red">{error}</div>}
        {status && (
          <ul className="mt-3 space-y-1.5 text-xs" data-testid="providers-list">
            {status.providers.map((p) => (
              <li key={p.name} className="flex items-start gap-2">
                <span className={p.configured ? "text-accent-green" : "text-content-3"} aria-hidden>●</span>
                <span className="text-content-2">
                  <span className="font-medium text-content-1">{p.name}</span>
                  {" — "}{LABELS[p.name] ?? p.capabilities.license_note}
                  {!p.configured && <span className="ml-1 text-accent-orange">(not configured)</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {status && (
          <div className="mt-2 text-xs text-content-3">
            Live order: {status.liveOrder.join(" → ") || "none"} · Historical: {status.historicalOrder.join(" → ") || "none"}
          </div>
        )}
      </div>

      <div className="mb-3 rounded-md bg-dark-700/50 p-4">
        <div className="text-sm font-medium">Massive API key</div>
        <div className="mt-0.5 text-xs text-content-3">
          Free "Options Basic" plan at massive.com. Stored locally in <code>providers.json</code>
          {" "}(plain text until v2.2 moves it to the OS keychain). {massiveSet ? "A key is set." : "No key yet."}
        </div>
        <div className="mt-2 flex items-end gap-2">
          <FormInput label="API key" type="password" value={key} placeholder={massiveSet ? "•••••••• (replace)" : "paste key"}
            onChange={(e) => setKey(e.target.value)} data-testid="massive-key" />
          <Button variant="secondary" size="sm" disabled={busy || !key.trim()} onClick={() => saveKey(key.trim())}>Save</Button>
          {massiveSet && (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => saveKey("")}>Remove</Button>
          )}
        </div>
      </div>

      <div className="mb-3 rounded-md bg-dark-700/50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-medium">Risk-free rate curve</div>
            <div className="mt-0.5 text-xs text-content-3" data-testid="rates-line">
              {rates
                ? rates.source === "treasury.gov"
                  ? `US Treasury par curve dated ${rates.date} (${rates.points} tenors), interpolated per expiry`
                  : `Fallback flat rate in use${rates.lastError ? ` — ${rates.lastError}` : ""}`
                : "Loading…"}
            </div>
          </div>
          <Button variant="secondary" size="sm" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.dataRatesRefresh();
                await refresh();
                showToast("✓ Rates refreshed");
              } catch (err) {
                showToast(friendlyError(err));
              } finally {
                setBusy(false);
              }
            }}>
            Refresh
          </Button>
        </div>
      </div>
    </>
  );
}
