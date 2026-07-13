import js from "@eslint/js";
import globals from "globals";

const correctnessRules = {
  "array-callback-return": "error",
  curly: ["error", "all"],
  eqeqeq: ["error", "always"],
  "no-implicit-coercion": "error",
  "no-promise-executor-return": "error",
  "no-unmodified-loop-condition": "error",
  "no-useless-call": "error",
  "prefer-const": "error",
  "prefer-template": "error",
  radix: "error",
};

export default [
  {
    ignores: ["node_modules/**", "controller/.venv/**"],
  },
  js.configs.recommended,
  {
    files: ["controller/src/devboxes_controller/static/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: globals.browser,
      sourceType: "script",
    },
    rules: correctnessRules,
  },
  {
    files: ["scripts/*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: globals.node,
      sourceType: "module",
    },
    rules: correctnessRules,
  },
];
