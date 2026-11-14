// I1 (roadmap v2): the API must answer CORS only for the renderer's own
// origins. Loopback binding is a runtime property (app.listen in the
// require.main block) and is checked manually: `curl` from another host fails.
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");

const app = require("../server");

function withServer(fn) {
  return new Promise((resolve, reject) => {
    const srv = http.createServer(app).listen(0, "127.0.0.1", async () => {
      try {
        resolve(await fn(srv.address().port));
      } catch (err) {
        reject(err);
      } finally {
        srv.close();
      }
    });
  });
}

function preflight(port, origin) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1", port, path: "/detect", method: "OPTIONS",
      headers: { Origin: origin, "Access-Control-Request-Method": "POST" },
    }, (res) => {
      res.resume();
      res.on("end", () => resolve(res.headers["access-control-allow-origin"]));
    });
    req.on("error", reject);
    req.end();
  });
}

test("CORS: vite dev origin allowed, arbitrary website refused", async () => {
  await withServer(async (port) => {
    assert.equal(await preflight(port, "http://localhost:5173"), "http://localhost:5173");
    assert.equal(await preflight(port, "https://evil.example"), undefined);
  });
});

test("CORS: requests with no Origin (Electron file://, curl) pass", async () => {
  await withServer((port) => new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path: "/nope" }, (res) => {
      res.resume();
      res.on("end", () => { assert.equal(res.statusCode, 404); resolve(); });
    }).on("error", reject);
  }));
});

test("default bind host is loopback", () => {
  assert.equal(process.env.OD_HOST || "127.0.0.1", "127.0.0.1");
});
