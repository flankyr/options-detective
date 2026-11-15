import { useState } from "react";
import type { Candidate, PositionMetrics } from "../../types";
import { money, pct, signed, strategyLabel } from "../../lib/format";
import { cx } from "../../lib/cx";

// Phase 6: every candidate can show the inputs behind its numbers. Nothing
// here is computed in the UI; it is the engine's evaluation block laid out.

function Row({ label, market, scenario, fmt }: {
  label: string; market: number | null | undefined; scenario?: number | null | undefined;
  fmt: (v: number) => string;
}) {
  const f = (v: number | null | undefined) => (v == null ? "—" : fmt(v));
  return (
    <tr>
      <td className="py-0.5 text-content-3">{label}</td>
      <td className="py-0.5 text-right font-mono text-content-2">{f(market)}</td>
      {scenario !== undefined && <td className="py-0.5 text-right font-mono text-content-1">{f(scenario)}</td>}
    </tr>
  );
}

export default function WhyThisNumber({ candidate: c }: { candidate: Candidate }) {
  const [open, setOpen] = useState(false);
  const ev = c.evaluation;
  if (!ev) return null;
  const m: PositionMetrics = ev.market;
  const s: PositionMetrics | null = ev.scenario;
  const sources = Array.from(new Set(c.legs.map((l) => l.ivSource).filter(Boolean)));
  return (
    <div className="rounded-md border border-dark-600/60" data-testid="why-this-number">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-left text-xs text-content-2 hover:text-content-1"
        aria-expanded={open}>
        <span>Why these numbers</span>
        <span className={cx("transition-transform", open && "rotate-180")}>⌄</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-dark-600/60 px-3 py-2 text-xs">
          <table className="w-full">
            <thead className="text-content-3">
              <tr>
                <th className="text-left font-normal">Per one lot</th>
                <th className="text-right font-normal">Market odds</th>
                {s && <th className="text-right font-normal">Your scenario</th>}
              </tr>
            </thead>
            <tbody>
              <Row label="Expected value (after costs)" market={m.ev} scenario={s?.ev} fmt={(v) => signed(v)} />
              <Row label="Probability of profit" market={m.pop} scenario={s?.pop} fmt={(v) => pct(v)} />
              <Row label="P(max profit)" market={m.probMaxProfit} scenario={s?.probMaxProfit} fmt={(v) => pct(v)} />
              <Row label="Worst 5% average (CVaR)" market={m.cvar05} scenario={s?.cvar05} fmt={(v) => money(v)} />
              <Row label="EV per $ of tail risk" market={m.evPerCvar} scenario={s?.evPerCvar} fmt={(v) => v.toFixed(3)} />
              <Row label="Kelly fraction" market={m.kellyFraction} scenario={s?.kellyFraction} fmt={(v) => pct(v)} />
              <Row label="Expected move at expiry" market={m.expectedMove} scenario={s?.expectedMove} fmt={(v) => `±${money(v)}`} />
            </tbody>
          </table>
          <p className="text-content-3">
            Market odds come from the option prices themselves (every fairly priced trade has EV of about minus its
            costs there). {s ? "Your scenario reshapes those odds with the volatility forecast and/or your view; that is where any edge comes from." : "Switch the screen out of Analyzer mode to add a forecast or a view."}
            {ev.approx && ` ${ev.approx}.`}
          </p>
          {c.costs && (
            <div className="text-content-3">
              Costs {money(c.costs.total)}: half-spreads {money(c.costs.spread)}, commissions {money(c.costs.commission)}
              {c.costs.assignment > 0 && `, expected assignment fees ${money(c.costs.assignment)}`}.
            </div>
          )}
          {c.sizing.binding && (
            <div className="text-content-3">
              Size: {c.sizing.contractsSuggested} lot{c.sizing.contractsSuggested === 1 ? "" : "s"}, limited by {c.sizing.binding}
              {c.sizing.limits && ` (risk budget ${c.sizing.limits.riskBudget ?? "—"}, buying power ${c.sizing.limits.buyingPower ?? "—"}${c.sizing.limits.kelly != null ? `, Kelly ${c.sizing.limits.kelly}` : ""})`}.
              {c.sizing.stressLossPerUnit != null && ` A ${c.sizing.stressSigmas ?? 2}σ move loses ${money(c.sizing.stressLossPerUnit)} per lot.`}
              {c.sizing.riskBudgetExceeded && " One lot exceeds your risk budget; shown so you can decide."}
              {c.sizing.capitalBasis && ` Capital basis: ${c.sizing.capitalBasis}.`}
            </div>
          )}
          <div className="text-content-3">
            Vol: {c.meta.volModel ?? "ATM"}{sources.length > 0 && ` (${sources.join(", ")} per leg)`} · σ {pct(c.meta.sigma)} · r {pct(c.meta.riskFreeRate, 2)}
            {c.meta.dividendYield != null && ` · q ${pct(c.meta.dividendYield, 2)}`}
            {c.meta.provider && ` · data ${c.meta.provider}${c.meta.asof ? ` as of ${c.meta.asof}` : ""}`}
            {c.meta.exerciseStyle && ` · ${c.meta.exerciseStyle} exercise`}
            {c.meta.exposure && ` · exposure Δ${c.meta.exposure.delta >= 0 ? "+" : "−"} vega${c.meta.exposure.vega >= 0 ? "+" : "−"} θ${c.meta.exposure.theta >= 0 ? "+" : "−"}`}
          </div>
          <div className="text-content-3">{strategyLabel(c.strategyType)} · {c.rationale}</div>
        </div>
      )}
    </div>
  );
}
