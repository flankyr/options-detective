import { useEffect, useMemo, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type BacktestResult, type StrategyInfo } from "../lib/api";
import { friendlyError } from "../lib/friendlyError";
import { money, pct, pnlClass, strategyLabel } from "../lib/format";
import { cssVar } from "../lib/cssVar";
import { useTheme } from "../contexts/ThemeContext";
import { useStore } from "../store";
import Button from "./ui/Button";
import { FormInput, FormSelect } from "./ui/Input";
import { Card, CardContent, CardHeader, MetricBox } from "./ui/Card";

// Research tab (Phase 5): run a strategy plug-in over archived chains with no
// look-ahead, read the equity curve, the metrics with their uncertainty, and
// the calibration of the model's own predictions. A result here is evidence;
// the screener's score is a hypothesis until this page agrees with it.

const fmt = (v: number | null | undefined, d = 2) => (v == null || Number.isNaN(v) ? "—" : v.toFixed(d));

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export default function Research() {
  useTheme();
  const showToast = useStore((s) => s.showToast);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategy, setStrategy] = useState("optimizer");
  const [paramsText, setParamsText] = useState("{}");
  const [symbol, setSymbol] = useState("SPY");
  const [start, setStart] = useState(daysAgo(365));
  const [end, setEnd] = useState(daysAgo(0));
  const [capital, setCapital] = useState("100000");
  const [screenEvery, setScreenEvery] = useState("5");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.researchStrategies().then((list) => {
      setStrategies(list);
      const cur = list.find((s) => s.name === strategy);
      if (cur) setParamsText(JSON.stringify(cur.params, null, 2));
    }).catch((err) => setError(friendlyError(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pickStrategy(name: string) {
    setStrategy(name);
    const s = strategies.find((x) => x.name === name);
    if (s) setParamsText(JSON.stringify(s.params, null, 2));
  }

  async function run() {
    let params: Record<string, unknown>;
    try {
      params = JSON.parse(paramsText || "{}");
    } catch {
      setError("Parameters must be valid JSON");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.researchBacktest({
        strategy, params,
        config: {
          symbol: symbol.trim().toUpperCase(), start, end,
          initialCapital: Number(capital) || 100000, screenEvery: Number(screenEvery) || 5,
        },
      });
      setResult(r);
      showToast(`✓ ${r.metrics.trades} trades over ${r.metrics.days} days in ${r.elapsedSeconds ?? "?"}s`);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  function exportCsv() {
    if (!result) return;
    const cols = ["id", "structure", "opened", "closed", "expiration", "reason", "contracts", "entryCost", "exitValue",
      "costs", "pnl", "pnlPerUnit", "daysHeld", "mae", "mfe", "predictedPop", "predictedEv", "predictedPopMarket"] as const;
    const lines = [cols.join(","), ...result.trades.map((t) => cols.map((c) => JSON.stringify(t[c] ?? "")).join(","))];
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `backtest-${result.strategy.name}-${result.config.symbol}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const doc = strategies.find((s) => s.name === strategy)?.doc;

  return (
    <div className="space-y-4" data-testid="research-view">
      <Card>
        <CardHeader>
          <div className="text-base font-medium">Backtest a strategy</div>
          <div className="text-xs text-content-3">
            Replays your archived chains day by day. On each date the strategy sees only that day's chain and
            price history, so there is no look-ahead. Costs: half-spreads, commissions, assignment fees.
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-3">
            <FormSelect label="Strategy" value={strategy} onChange={(e) => pickStrategy(e.target.value)} data-testid="research-strategy">
              {strategies.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </FormSelect>
            <FormInput label="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
            <FormInput label="Initial capital ($)" value={capital} onChange={(e) => setCapital(e.target.value)} inputMode="numeric" />
            <FormInput label="Start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            <FormInput label="End" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            <FormInput label="Screen every N trading days" value={screenEvery} onChange={(e) => setScreenEvery(e.target.value)}
              hint="Marks run daily; screening is the expensive step" inputMode="numeric" />
          </div>
          {doc && <div className="mt-3 text-xs text-content-3">{doc}</div>}
          <label className="mt-3 block text-xs text-content-3">
            Parameters (JSON)
            <textarea value={paramsText} onChange={(e) => setParamsText(e.target.value)} rows={6}
              className="mt-1 w-full rounded-md border border-dark-600 bg-dark-800 p-2 font-mono text-xs text-content-1"
              data-testid="research-params" />
          </label>
          <div className="mt-3 flex items-center gap-3">
            <Button onClick={run} disabled={busy} data-testid="research-run">{busy ? "Running…" : "Run backtest"}</Button>
            {result && <Button variant="secondary" size="sm" onClick={exportCsv}>Export trades CSV</Button>}
            {error && <span className="text-xs text-accent-red">{error}</span>}
          </div>
        </CardContent>
      </Card>

      {result && <Results result={result} />}
    </div>
  );
}

function Results({ result }: { result: BacktestResult }) {
  const m = result.metrics;
  const line = cssVar("--od-accent-primary", "#9733FF");
  const grid = cssVar("--od-dark-700", "#252535");
  const axis = cssVar("--od-text-3", "#9e9eb2");
  const curve = useMemo(() => result.curve.map((p) => ({ ...p, equity: Math.round(p.equity) })), [result]);
  const ci = m.sharpeCI;

  return (
    <>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-8" data-testid="research-metrics">
        <MetricBox label="Return" value={pct(m.totalReturn ?? 0)} highlight={(m.totalReturn ?? 0) >= 0 ? "green" : "red"} />
        <MetricBox label="CAGR" value={m.cagr == null ? "—" : pct(m.cagr)} />
        <MetricBox label="Sharpe" value={fmt(m.sharpe)} hint={ci ? `90% bootstrap CI ${fmt(ci[0])} to ${fmt(ci[1])}` : "Too few days for a confidence interval"} />
        <MetricBox label="Max DD" value={m.maxDrawdown == null ? "—" : pct(m.maxDrawdown)} highlight="red" />
        <MetricBox label="Win rate" value={m.winRate == null ? "—" : pct(m.winRate)} />
        <MetricBox label="Profit factor" value={fmt(m.profitFactor)} />
        <MetricBox label="Trades" value={m.trades} />
        <MetricBox label="Costs" value={money(m.totalCosts ?? 0)} />
      </div>
      {ci && (
        <div className="text-xs text-content-3">
          Sharpe {fmt(m.sharpe)} with a 90% bootstrap interval of {fmt(ci[0])} to {fmt(ci[1])}
          {ci[0] <= 0 && ci[1] >= 0 ? " — the interval includes zero: this run does not show a reliable edge." : "."}
          {result.skippedDays.length > 0 && ` ${result.skippedDays.length} day(s) skipped (no chain).`}
        </div>
      )}

      <Card>
        <CardHeader><div className="text-sm font-medium">Equity curve</div></CardHeader>
        <CardContent>
          <div className="h-56 w-full" data-testid="research-curve">
            <ResponsiveContainer>
              <AreaChart data={curve} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="research-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={line} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={line} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={grid} opacity={0.2} />
                <XAxis dataKey="date" tickFormatter={(v: string) => v.slice(0, 7)} stroke={axis} fontSize={11} minTickGap={40} />
                <YAxis tickFormatter={(v: number) => money(v)} stroke={axis} fontSize={11} width={80} domain={["auto", "auto"]} />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload as BacktestResult["curve"][number];
                  return (
                    <div className="rounded-lg border border-dark-600 bg-dark-800 px-3 py-2 text-xs shadow-glass">
                      <div className="font-medium text-content-1">{p.date}</div>
                      <div>Equity {money(p.equity)} · {p.open} open · spot {p.spot}</div>
                    </div>
                  );
                }} />
                <Area type="monotone" dataKey="equity" stroke={line} strokeWidth={2} fill="url(#research-fill)" isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="text-sm font-medium">Calibration: predicted POP vs realized</div>
            <div className="text-xs text-content-3">
              A 70% bucket should win about 70% of the time. Brier score: 0 is perfect, 0.25 is a coin flip.
            </div>
          </CardHeader>
          <CardContent>
            {result.calibration.pop ? (
              <table className="w-full text-xs" data-testid="research-calibration">
                <thead className="text-content-3"><tr><th className="text-left font-normal">Bucket</th><th className="text-right font-normal">n</th><th className="text-right font-normal">Predicted</th><th className="text-right font-normal">Realized</th></tr></thead>
                <tbody className="font-mono text-content-2">
                  {result.calibration.pop.buckets.map((b) => (
                    <tr key={b.bucket}><td>{b.bucket}</td><td className="text-right">{b.n}</td><td className="text-right">{pct(b.predicted)}</td><td className="text-right">{pct(b.realized)}</td></tr>
                  ))}
                </tbody>
                <tfoot className="text-content-3"><tr><td colSpan={4} className="pt-1">
                  Brier {fmt(result.calibration.pop.brier, 3)} (ranking distribution)
                  {result.calibration.popMarket && ` · ${fmt(result.calibration.popMarket.brier, 3)} (market density)`}
                </td></tr></tfoot>
              </table>
            ) : <div className="text-xs text-content-3">No predictions to calibrate for this strategy.</div>}
            {result.calibration.ev && (
              <div className="mt-2 text-xs text-content-3">
                EV: predicted {money(result.calibration.ev.meanPredicted)} vs realized {money(result.calibration.ev.meanRealized)} per unit
                (bias {money(result.calibration.ev.bias)}; slope {fmt(result.calibration.ev.slope)}; a slope near 1 means the EV scale is believable).
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><div className="text-sm font-medium">Trades ({result.trades.length})</div></CardHeader>
          <CardContent>
            <div className="max-h-72 overflow-auto">
              <table className="w-full text-xs" data-testid="research-trades">
                <thead className="text-content-3"><tr>
                  <th className="text-left font-normal">Structure</th><th className="text-left font-normal">Opened</th>
                  <th className="text-left font-normal">Closed</th><th className="text-left font-normal">Why</th>
                  <th className="text-right font-normal">POP</th><th className="text-right font-normal">P&amp;L</th>
                </tr></thead>
                <tbody className="font-mono text-content-2">
                  {result.trades.map((t) => (
                    <tr key={t.id}>
                      <td>{strategyLabel(t.structure)} ×{t.contracts}</td><td>{t.opened}</td><td>{t.closed}</td><td>{t.reason}</td>
                      <td className="text-right">{t.predictedPop == null ? "—" : pct(t.predictedPop)}</td>
                      <td className={`text-right ${pnlClass(t.pnl)}`}>{money(t.pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
