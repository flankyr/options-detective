// Phase 5: research routes validate input and serialise one job at a time.
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");

const app = require("../server");
const { engineTest } = require("./engineAvailable");

function call(port, method, path, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path, method, headers: { "Content-Type": "application/json" } }, (res) => {
      let data = "";
      res.on("data", (c) => { data += c; });
      res.on("end", () => resolve({ status: res.statusCode, body: data ? JSON.parse(data) : null }));
    });
    req.on("error", reject);
    if (body !== undefined) req.write(JSON.stringify(body));
    req.end();
  });
}

function withServer(fn) {
  return new Promise((resolve, reject) => {
    const srv = http.createServer(app).listen(0, "127.0.0.1", async () => {
      try { resolve(await fn(srv.address().port)); } catch (e) { reject(e); } finally { srv.close(); }
    });
  });
}

test("research: input validation", async () => {
  await withServer(async (port) => {
    const r = await call(port, "POST", "/research/backtest", { strategy: "optimizer" });
    assert.equal(r.status, 400);
    const s = await call(port, "GET", "/research/status");
    assert.deepEqual(s.body, { running: false });
  });
});

test("research: strategies are listed from the Python registry", engineTest, async () => {
  await withServer(async (port) => {
    const r = await call(port, "GET", "/research/strategies");
    assert.equal(r.status, 200);
    const names = r.body.map((s) => s.name);
    for (const n of ["optimizer", "strangle_16d", "put_spread_vrp", "buy_and_hold"]) assert.ok(names.includes(n), n);
    assert.ok(r.body.every((s) => typeof s.doc === "string" && s.params));
  });
});

test("research: a backtest on a symbol with no archive fails cleanly", engineTest, async () => {
  await withServer(async (port) => {
    const r = await call(port, "POST", "/research/backtest", {
      strategy: "buy_and_hold", params: {},
      config: { symbol: "NOPE", start: "2026-01-05", end: "2026-01-09", useForecast: false, initialCapital: 1000 },
    });
    assert.equal(r.status, 400);
    assert.match(r.body.error, /no chain could be loaded|no archived|no source/i);
  });
});
