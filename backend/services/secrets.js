// Provider credentials (Phase 7). Kept in process memory and handed to each
// Python spawn through the environment; never written to disk by the backend.
//   packaged app: Electron decrypts the key with safeStorage at startup, passes
//                 MASSIVE_API_KEY in the env, and forwards runtime changes here
//                 (OD_SECRETS_MANAGED=1 tells us not to persist)
//   dev / plain node: PUT /data/providers persists to providers.json as before
const store = new Map();
if (process.env.MASSIVE_API_KEY) store.set("massiveApiKey", process.env.MASSIVE_API_KEY);

const ENV_NAMES = { massiveApiKey: "MASSIVE_API_KEY" };

function set(name, value) {
  if (value) store.set(name, String(value));
  else store.delete(name);
}

function get(name) {
  return store.get(name) ?? null;
}

function managed() {
  return process.env.OD_SECRETS_MANAGED === "1";
}

// Environment for a Python child: parent env + every known secret.
function childEnv() {
  const env = { ...process.env };
  for (const [name, envName] of Object.entries(ENV_NAMES)) {
    const v = store.get(name);
    if (v) env[envName] = v;
    else delete env[envName];
  }
  return env;
}

module.exports = { set, get, managed, childEnv, ENV_NAMES };
