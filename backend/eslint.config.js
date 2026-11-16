// Flat config (ESLint 9) for the CommonJS backend. `npm run lint` in backend/.
const js = require("@eslint/js");
const globals = require("globals");

module.exports = [
  { ignores: ["node_modules", "data", "math"] },
  js.configs.recommended,
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "commonjs",
      globals: { ...globals.node },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
];
