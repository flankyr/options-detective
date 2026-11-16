import type { ScreenResult } from "../../types";
import { pct } from "../../lib/format";

// Phase 6: what the screen was ranked on, and the volatility evidence behind it.
export default function ScreenContextBanner({ result }: { result: ScreenResult }) {
  if (!result.mode) return null;
  const v = result.vrp;
  const f = result.forecast;
  const label: Record<string, string> = {
    market: "market odds (Analyzer): no edge assumed, EV ≈ −costs everywhere",
    vrp: "volatility premium: realized-vol forecast vs implied",
    directional: `your ${result.directionalView} view`,
    both: `volatility premium plus your ${result.directionalView} view`,
  };
  return (
    <div className="rounded-md border border-accent-blue/30 bg-accent-blue/10 px-4 py-2 text-sm text-content-2" data-testid="screen-context">
      <div>Ranked on <b>{label[result.mode] ?? result.mode}</b>.</div>
      {v && f && (
        <div className="mt-1 text-xs text-content-3">
          ATM implied vol {pct(v.ivAtm, 1)} vs {f.method.toUpperCase()} forecast {pct(v.sigmaForecast, 1)}
          {f.sigmaLo != null && f.sigmaHi != null && ` (band ${pct(f.sigmaLo, 1)}–${pct(f.sigmaHi, 1)})`}
          {" · "}ratio {v.ratio.toFixed(2)}
          {v.z != null ? ` · z ${v.z.toFixed(2)} vs this symbol's history` : " · no VRP history yet (archive accumulates it)"}
          {" · "}
          {v.sign > 0 ? "options look rich: short-vega structures eligible" : v.sign < 0 ? "options look cheap: long-vega structures eligible" : "flat: both sides screened"}
        </div>
      )}
      {result.provider && (
        <div className="mt-1 text-xs text-content-3">
          Data {result.provider}{result.asof ? ` as of ${result.asof}` : ""} · score v{result.scoreVersion ?? 1}
        </div>
      )}
    </div>
  );
}
