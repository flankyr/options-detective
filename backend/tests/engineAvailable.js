// Is the Python math engine runnable on this machine? The engine-dependent
// suites (mathEngine, detector, calculator, one recommender test) skip with
// an explicit reason when it is not, instead of failing 21 tests with
// "math engine exited unexpectedly" that read like broken code. CI installs
// the venv, so there a skip would itself be the bug: set OD_REQUIRE_ENGINE=1
// to turn "unavailable" into a hard failure.
const { spawnSync } = require("child_process");

const { pythonBin } = require("../services/python");

let cached;

function engineAvailable() {
  if (cached !== undefined) return cached;
  if (process.env.OD_MATH_BIN) {
    cached = true;
    return cached;
  }
  const probe = spawnSync(pythonBin(), ["-c", "import scipy, numpy"], {
    timeout: 20_000, windowsHide: true, encoding: "utf8",
  });
  cached = probe.status === 0;
  if (!cached && process.env.OD_REQUIRE_ENGINE) {
    throw new Error(`math engine required but unavailable: ${probe.stderr || probe.error}`);
  }
  return cached;
}

// Pass as the options argument of node:test's test(): `test(name, engineTest, fn)`
const engineTest = {
  get skip() {
    return engineAvailable()
      ? false
      : "math venv not found: cd backend/math && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt";
  },
};

module.exports = { engineAvailable, engineTest };
