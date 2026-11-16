// Plain-language copy for beginner mode (updated brief §4.3). Static
// presentation text keyed by strategy — no numbers are ever derived here.
import type { StrategyType } from "../types";

export const ALL_STRATEGY_TYPES: StrategyType[] = [
  "covered_call", "cash_secured_put", "call_vertical", "put_vertical",
  "iron_condor", "long_straddle", "short_strangle",
];

export const BEST_FOR: Record<StrategyType, string> = {
  covered_call: "Earning extra income on shares you already own.",
  cash_secured_put: "Getting paid to wait for a chance to buy the stock cheaper.",
  call_vertical: "A defined-risk bet that the stock goes up.",
  put_vertical: "A defined-risk bet that the stock goes down.",
  iron_condor: "Betting the stock stays inside a range while time passes.",
  long_straddle: "Betting on a big move in either direction.",
  short_strangle: "Collecting premium if the stock stays calm, risk is unlimited.",
  long_call: "A simple bet the stock rises; you can only lose the premium.",
  long_put: "A simple bet the stock falls; you can only lose the premium.",
  call_credit_spread: "Getting paid if the stock stays below a level; defined risk.",
  put_credit_spread: "Getting paid if the stock stays above a level; defined risk.",
  iron_butterfly: "Collecting the most premium if the stock pins near today's price.",
  short_straddle: "Selling a big move in either direction; risk is unlimited.",
  long_strangle: "A cheaper bet on a big move in either direction.",
  calendar: "Selling near-term time decay while owning a longer-dated option.",
  diagonal: "A covered-call-like position using a long-dated call instead of shares.",
  call_ratio_spread: "Paid for a modest rise; risk grows if the stock runs far higher.",
};
