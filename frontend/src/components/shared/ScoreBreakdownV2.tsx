import type { Candidate, ScoreComponentKeyV2 } from "../../types";
import { pct } from "../../lib/format";
import Hint from "../ui/Hint";
import { cx } from "../../lib/cx";

// Phase 6: the score v2 breakdown (optimizer.WEIGHTS). Fixed weights, every
// component in [0, 1] and independent of position size. The bar shows each
// component's contribution to the 0-10 composite; hover explains what it
// measures and how this candidate did.

export const V2_META: Record<ScoreComponentKeyV2, { label: string; explain: string; color: string }> = {
  evCvar: {
    label: "EV / tail risk",
    explain: "Expected value after costs divided by the average loss in the worst 5% of outcomes (CVaR), under the ranking distribution. 0.5 means EV equals half the tail loss and scores full marks; a negative EV scores below the midpoint.",
    color: "bg-accent-primary",
  },
  pop: {
    label: "POP",
    explain: "Probability of any profit under the ranking distribution. Shown, and lightly weighted, because POP alone is not edge: the market prices high-POP trades to have EV of zero.",
    color: "bg-accent-blue",
  },
  theta: {
    label: "θ / capital",
    explain: "Annualised net time decay per dollar of capital at risk, so a one-lot and a ten-lot score the same. Collectors score above the midpoint, payers below.",
    color: "bg-accent-green",
  },
  liquidity: {
    label: "Liquidity",
    explain: "Bid-ask tightness (half), open interest and volume relative to a 500-contract reference (a quarter each). Unknown liquidity (historical data without volume) scores the midpoint.",
    color: "bg-accent-cyan",
  },
  confidence: {
    label: "Model confidence",
    explain: "How much to trust the numbers: smile fit error, width of the realized-vol forecast band, and data freshness, averaged.",
    color: "bg-accent-orange",
  },
};

const ORDER: ScoreComponentKeyV2[] = ["evCvar", "pop", "theta", "liquidity", "confidence"];

export function isV2(c: Candidate): boolean {
  const b = c.scoreBreakdown;
  return Boolean(b && (b.version === 2 || "evCvar" in b.components));
}

export default function ScoreBreakdownV2({ candidate: c }: { candidate: Candidate }) {
  const b = c.scoreBreakdown;
  if (!b || !isV2(c)) return null;
  const comps = b.components as Record<ScoreComponentKeyV2, number>;
  const weights = b.weights as Record<ScoreComponentKeyV2, number>;
  const parts = ORDER.map((k) => ({ key: k, component: comps[k] ?? 0, points: (weights[k] ?? 0) * (comps[k] ?? 0) * 10 }));
  return (
    <div data-testid="score-breakdown-v2">
      <div className="mb-1 flex items-center justify-between text-xs text-content-3">
        <span>Score v2 · ranked on {c.evaluation?.ranking === "scenario" ? "your scenario" : "market odds"}</span>
        <span className="font-mono font-semibold text-content-2">{c.compositeScore.toFixed(2)} / 10</span>
      </div>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-dark-700">
        {parts.map((p) => (
          <div key={p.key} className={cx(V2_META[p.key].color, "h-full")} style={{ width: `${(p.points / 10) * 100}%` }} />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
        {parts.map((p) => (
          <Hint key={p.key} text={`${V2_META[p.key].explain} This candidate: ${pct(p.component)}, ${p.points.toFixed(2)} points.`}>
            <span className="inline-flex cursor-help items-center gap-1 text-[11px] text-content-3">
              <span className={cx("h-2 w-2 rounded-full", V2_META[p.key].color)} />
              {V2_META[p.key].label}
              <span className="font-mono text-content-2">{p.points.toFixed(1)}</span>
            </span>
          </Hint>
        ))}
      </div>
    </div>
  );
}
