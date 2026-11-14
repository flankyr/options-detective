// One way to run a one-shot od-math entry point (market_data, archive, ...):
// spawn, pipe a JSON request on stdin, parse the {ok, result|error} envelope.
// dataLayer/marketPulse/etf* grew their own copies of this loop; they migrate
// here in Phase 1 (D5). New code uses this.
const { spawn } = require("child_process");

const { mathCommand } = require("./python");

class PythonDomainError extends Error {}

function runPython(entry, request, { timeoutMs = 120_000, extraArgs = [] } = {}) {
  return new Promise((resolve, reject) => {
    const cmd = mathCommand(entry, extraArgs);
    const proc = spawn(cmd.bin, cmd.args, { cwd: cmd.cwd, windowsHide: true });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      proc.kill();
      reject(new Error(`${entry} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    proc.stdout.on("data", (c) => { stdout += c; });
    proc.stderr.on("data", (c) => { stderr += c; });
    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`failed to start ${entry}: ${err.message}`));
    });
    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`${entry} exited with code ${code}: ${stderr.slice(0, 500)}`));
        return;
      }
      let parsed;
      try {
        parsed = JSON.parse(stdout);
      } catch {
        reject(new Error(`${entry} returned invalid JSON: ${stdout.slice(0, 200)}`));
        return;
      }
      if (!parsed.ok) {
        reject(new PythonDomainError(parsed.error));
        return;
      }
      resolve(parsed.result);
    });
    proc.stdin.write(JSON.stringify(request));
    proc.stdin.end();
  });
}

module.exports = { runPython, PythonDomainError };
