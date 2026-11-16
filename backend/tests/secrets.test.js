const test = require("node:test");
const assert = require("node:assert/strict");

const secrets = require("../services/secrets");

test("secrets: in-memory store reaches child env and never persists", () => {
  secrets.set("massiveApiKey", "k-123");
  assert.equal(secrets.get("massiveApiKey"), "k-123");
  assert.equal(secrets.childEnv().MASSIVE_API_KEY, "k-123");
  secrets.set("massiveApiKey", null);
  assert.equal(secrets.get("massiveApiKey"), null);
  assert.equal(secrets.childEnv().MASSIVE_API_KEY, undefined);
  assert.equal(secrets.managed(), process.env.OD_SECRETS_MANAGED === "1");
});
