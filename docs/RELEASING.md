# Developing and releasing Option Obelisk

## Day-to-day

```powershell
# backend (terminal 1) — listens on 127.0.0.1:3001 only
cd backend; npm run dev

# frontend with hot reload (terminal 2)
cd frontend; npm run dev

# electron shell pointed at the dev server (terminal 3)
cd electron
$env:VITE_DEV_SERVER_URL = "http://localhost:5173"; npm start
```

Isolated second instance (different ports, separate data dir) for comparing
builds side by side: `.claude/launch.json` defines `backend-alt` (3002) and
`frontend-alt` (5174, `--mode isolated` reads `frontend/.env.isolated`).

## Tests

```powershell
cd backend/math; .venv\Scripts\python.exe -m pytest -q      # math engine
cd backend; npm test                                        # services (engine suites skip without the venv)
cd frontend; npx tsc --noEmit -p .                          # types
```

CI runs all three on Windows for every PR (`.github/workflows/ci.yml`). In CI
the engine suites must run: `OD_REQUIRE_ENGINE=1` turns a skip into a failure.

## Lint and format

```powershell
cd backend;  npm run lint; npm run format
cd frontend; npm run lint; npm run format
cd backend/math; .venv\Scripts\ruff.exe check .; .venv\Scripts\black.exe .
```

## Release

```powershell
cd C:\Users\usmaa\Documents\Usmaan\ClaudeProjects\OptionsAi

# 1. bump "version" in backend/, frontend/, electron/ package.json (keep them equal)
# 2. write docs/releases/vX.Y.Z.md

# 3. build
cd frontend; npm run build
cd ../backend; npm run build:math          # PyInstaller -> backend/math/dist/od-math/
cd ../electron; npm run dist               # NSIS -> electron/release/Option-Obelisk-Setup-X.Y.Z.exe
cd ..

# 4. tag + GitHub release
git tag vX.Y.Z; git push origin vX.Y.Z
gh release create vX.Y.Z "electron/release/Option-Obelisk-Setup-X.Y.Z.exe" `
  --title "Option Obelisk vX.Y.Z" --notes-file docs/releases/vX.Y.Z.md
gh release view vX.Y.Z
```

## Code signing (Phase 7, owner action)

Unsigned installers trip SmartScreen ("More info → Run anyway") and the
auto-updater cannot verify downloads. Two routes:

1. **Azure Trusted Signing** (~US$10/month, no hardware token). Create a
   Trusted Signing account and certificate profile, install the
   `Microsoft.Trusted.Signing.Client`, then in `electron/package.json` set
   `build.win.signtoolOptions` / `azureSignOptions` per electron-builder's
   docs and remove `"signAndEditExecutable": false`.
2. **OV code-signing certificate** (~US$200–400/yr, hardware token since 2023).
   Set `build.win.certificateSubjectName` to the certificate's subject and
   let electron-builder call signtool; remove `"signAndEditExecutable": false`.

Either way, CI needs the credentials as repository secrets; never commit them.

## Auto-update

`electron-updater` checks GitHub Releases (`build.publish`) on launch of a
packaged build and installs on quit. `npm run dist` must therefore be run
with `GH_TOKEN` set (or `electron-builder --publish always`) so the
`latest.yml` manifest is uploaded beside the installer; without it clients
see no update. The repository in `build.publish` is `kmptrades/option-obelisk`
(after the rename); GitHub redirects the old name for clones but the updater
needs the current one.

## Credentials

The packaged app encrypts provider keys with Electron `safeStorage` (DPAPI
on Windows) into `%APPDATA%\Option Obelisk\secrets.json` and passes them to
the backend only through its environment. The dev server, which has no
keychain, keeps them in `backend/data/providers.json` (gitignored).

## Chain archive (Phase 0)

The backend snapshots the watchlist's option chains once per trading day
after the 16:00 ET close, to `<OD_DATA_DIR>/archive/<SYMBOL>/<date>.parquet`
(`%APPDATA%\Option Obelisk\archive` in the packaged app; `backend/data/archive` in dev). It runs
whenever the app is open and the day's file is missing. To cover days the
app is never opened, add a Task Scheduler entry at 16:30 ET on weekdays:

```powershell
cd backend/math
echo {"symbols":["SPY","QQQ"],"root":"<OD_DATA_DIR>\archive"} | .venv\Scripts\python.exe archive.py
```

Settings → Data in the app shows coverage and runs a snapshot on demand.
