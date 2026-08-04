import js from "@eslint/js";
import globals from "globals";

export default [
	js.configs.recommended,
	{
		languageOptions: {
			ecmaVersion: 2022,
			sourceType: "script",
			globals: {
				...globals.browser,
				// HTMX global
				htmx: "readonly",
			},
		},
		rules: {
			// Error prevention
			"no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
			"no-undef": "error",
			"no-console": ["warn", { allow: ["warn", "error", "log"] }],

			// Best practices
			"eqeqeq": ["error", "always"],
			"no-eval": "off", // Used for data-init attribute processing
			"no-implied-eval": "error",
			"no-new-func": "error",
			"no-return-assign": "error",
			"no-self-compare": "error",
			"no-throw-literal": "error",
			"no-useless-concat": "error",
			"prefer-const": "error",

			// Style (minimal, not conflicting with existing code)
			"semi": ["error", "always"],
			"no-trailing-spaces": "error",
			"no-multiple-empty-lines": ["error", { max: 2 }],
		},
	},
	{
		// Specific rules for capture-stats.js which uses ECharts and extends CaptureStats
		files: ["**/capture-stats.js"],
		languageOptions: {
			globals: {
				echarts: "readonly",
				CaptureStats: "writable", // Defined on window in this file
			},
		},
	},
];
