import type { ScreenMode } from "../../types";
import { cx } from "../../lib/cx";
import Hint from "../ui/Hint";

// Phase 6: which distribution ranks the screen. Analyzer = the market's own
// odds (honest baseline, no edge claimed). The other three reshape those
// odds with a realized-vol forecast and/or the user's directional view.

export const MODES: Array<{ id: ScreenMode; label: string; hint: string }> = [
  { id: "market", label: "Analyzer", hint: "Market odds only. Every fairly priced trade shows EV of about minus its costs; the list is ordered by liquidity, decay and confidence. Nothing is recommended." },
  { id: "vrp", label: "Vol premium", hint: "Rank by expected value when realized volatility follows the forecast (HAR-RV) instead of what options imply. Sells premium when IV is rich, buys it when cheap." },
  { id: "directional", label: "My view", hint: "Rank by expected value when the forward drifts in the direction of your view, scaled by your conviction (1 = a full one-sigma move)." },
  { id: "both", label: "Both", hint: "Forecast volatility and your view together." },
];

export default function ModeSwitch({ mode, conviction, view, onMode, onConviction }: {
  mode: ScreenMode; conviction: number; view: string;
  onMode: (m: ScreenMode) => void; onConviction: (c: number) => void;
}) {
  const directional = (mode === "directional" || mode === "both") && (view === "bullish" || view === "bearish");
  return (
    <div className="flex flex-wrap items-end gap-3" data-testid="mode-switch">
      <div>
        <div className="mb-1 text-xs text-content-3">Rank by</div>
        <div className="flex gap-1" role="radiogroup" aria-label="Ranking distribution">
          {MODES.map((m) => (
            <Hint key={m.id} text={m.hint}>
              <button type="button" role="radio" aria-checked={mode === m.id} data-mode={m.id}
                onClick={() => onMode(m.id)}
                className={cx(
                  "rounded border px-2.5 py-1.5 text-xs transition-all duration-150 ease-out-quad",
                  mode === m.id
                    ? "border-accent-primary/60 bg-accent-primary/15 text-accent-primary-text"
                    : "border-dark-600 text-content-3 hover:border-dark-500 hover:text-content-2",
                )}>
                {m.label}
              </button>
            </Hint>
          ))}
        </div>
      </div>
      {(mode === "directional" || mode === "both") && (
        <label className="text-xs text-content-3">
          Conviction {Math.round(conviction * 100)}%
          <input type="range" min={0} max={100} step={5} value={Math.round(conviction * 100)}
            onChange={(e) => onConviction(Number(e.target.value) / 100)}
            className="mt-1 block w-32 accent-accent-primary" data-testid="conviction"
            title={directional ? "How far the forward drifts in your direction, as a fraction of one sigma" : "Set a bullish or bearish view to apply conviction"} />
        </label>
      )}
    </div>
  );
}
