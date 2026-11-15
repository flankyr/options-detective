import { useEffect, useState } from "react";
import Button from "../ui/Button";

// Phase 7: the plain-language terms every user should read once, and a
// permanent footer line. Not legal advice to the author either: review the
// wording with counsel before anything is sold.

const ACK_KEY = "od.disclaimerAck.v1";

export const DISCLAIMER_SHORT =
  "Educational analytics, not investment advice. Options involve substantial risk and are not suitable for every investor. "
  + "Market data is delayed and may be wrong; every number is a model output. You alone are responsible for your trades.";

export function hasAcknowledged(): boolean {
  try { return localStorage.getItem(ACK_KEY) === "1"; } catch { return true; }
}

export function DisclaimerFooter() {
  return (
    <footer className="mx-auto max-w-7xl px-6 pb-6 pt-2 text-[11px] leading-relaxed text-content-3" data-testid="disclaimer-footer">
      {DISCLAIMER_SHORT} Data: Massive (formerly Polygon.io) and Yahoo Finance via yfinance for live chains, 15 minutes
      delayed; your own daily archive and the DoltHub <code>post-no-preference/options</code> dataset for history;
      US Treasury par yields for rates. Option Obelisk is not affiliated with any of these providers.
    </footer>
  );
}

export function DisclaimerGate({ children }: { children: React.ReactNode }) {
  const [ack, setAck] = useState(hasAcknowledged());
  useEffect(() => { setAck(hasAcknowledged()); }, []);
  if (ack) return <>{children}</>;
  return (
    <div className="flex min-h-screen items-center justify-center bg-dark-900 p-6" data-testid="disclaimer-gate">
      <div className="card-glass max-w-xl space-y-4 p-6 text-sm text-content-2">
        <h1 className="text-lg font-semibold text-content-1">Before you start</h1>
        <p>
          Option Obelisk is an <b>educational analytics tool</b>. It is not a broker, it does not execute trades, and
          nothing it shows is investment, legal or tax advice. Options involve substantial risk, including the loss
          of more than your initial investment on undefined-risk positions.
        </p>
        <p>
          Every price, probability, expected value and recommendation is a <b>model output</b> built on delayed
          third-party data that may be incomplete or wrong. Where the app says a strategy has positive expected value,
          that holds only if its volatility forecast or your stated view turns out to be right; the Research tab shows
          how often that has happened. Past results, simulated or real, do not predict future results.
        </p>
        <p>You are solely responsible for any trading decision. Verify live quotes with your broker before trading.</p>
        <div className="flex items-center gap-3 pt-2">
          <Button onClick={() => { try { localStorage.setItem(ACK_KEY, "1"); } catch { /* ignore */ } setAck(true); }}
            data-testid="disclaimer-accept">
            I understand
          </Button>
          <span className="text-xs text-content-3">Shown once per device. Full text in Help → Glossary → Disclaimer.</span>
        </div>
      </div>
    </div>
  );
}
