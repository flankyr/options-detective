@echo off
REM Phase 0 branch cleanup. Every branch below is fully merged into main
REM (verified with `git branch --merged main` on 2026-11-14). Run from the
REM repo root AFTER renaming the repo and merging the p0 PRs. Deletes locally
REM and on origin. Review the list before running.
set BRANCHES=chore/v1.9.3-optimizations dev feature/algorithm-explainer feature/etf-screener feature/filters-sorts feature/index-component-screener feature/journal-phase-a feature/paper-trading feature/pre-launch-suite feature/scoring-weights feature/v1.10.2-ux-polish feature/v1.10.3-opal feature/v1.10.4 feature/v1.10.5 feature/v1.10.6 feature/v1.4.0-obsidian-overhaul feature/v1.5.0-aesthetic-qol feature/v1.5.1-ux-polish feature/v1.6.0-local-accounts feature/v1.9.0-etf-currency fix/v1.3.1-calculator-ux fix/v1.3.2-paper-marks fix/v1.3.3-paper-qol fix/v1.5.2-payoff-labels master ux/phase1-buttons-cards ux/phase2-charts-forms ux/phase3-transitions ux/phase4-themes ux/phase5-polish
for %%b in (%BRANCHES%) do (
  git branch -D %%b 2>nul
  git push origin --delete %%b 2>nul
)
git fetch --prune
git branch -a
