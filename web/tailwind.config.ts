import type { Config } from "tailwindcss";
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "var(--ink)", panel: "var(--panel)", raised: "var(--raised)", line: "var(--line)",
        text: "var(--text)", mute: "var(--mute)", dim: "var(--dim)",
        amber: "var(--amber)", up: "var(--up)", down: "var(--down)", info: "var(--info)", warn: "var(--warn)",
      },
      fontFamily: {
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
      fontSize: { "2xs": ["10.5px", "14px"] },
    },
  },
} satisfies Config;
