import type { Config } from "tailwindcss";

// ─── "Marigold Ledger" design system ────────────────────────────────────────
// Dark, warm control-room aesthetic for an Indian insurance call desk.
// `ink` is a dark-surface scale (50 = page background … 900 = brightest text),
// `brand` is marigold/saffron, `jade` is the secondary (agent voice) accent.
const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark surface + text scale (inverted vs. a light theme: 50 is the
        // deepest background, 900 is the brightest foreground).
        ink: {
          50: "#0E1014", // page background
          100: "#161922", // subtle fill / hover
          150: "#1B1F2A", // card surface
          200: "#262B38", // hairline borders
          300: "#39404F", // strong borders
          400: "#707888", // faint text
          500: "#959CAA", // secondary text
          600: "#B3B8C2",
          700: "#CDD0D7",
          800: "#E1E1DD",
          900: "#F2EFE7", // primary text (warm off-white)
        },
        // Marigold / saffron — primary accent.
        brand: {
          50: "#2B2113", // saffron-tinted dark fill
          100: "#3A2B15",
          200: "#553D1B", // saffron-tinted border
          300: "#8A6024",
          400: "#C98E2E",
          500: "#E9A83D", // core marigold
          600: "#F2B655",
          700: "#F8C97E", // bright marigold text on dark
          800: "#FBDCA8",
          900: "#FDEDD2",
        },
        // Jade — secondary accent (agent voice, success-adjacent).
        jade: {
          50: "#10211C",
          100: "#152B24",
          200: "#1E4034",
          300: "#2A6450",
          400: "#33A37D",
          500: "#3FC096",
          600: "#62D1AC",
          700: "#8ADFC2",
          800: "#B4ECD9",
        },
        // Status colors tuned for dark surfaces.
        ok: { 400: "#6FD195", 500: "#46BA74", 600: "#2F9558" },
        warn: { 400: "#EFC368", 500: "#E0A93C", 600: "#BD8723" },
        danger: { 300: "#F8A8A4", 400: "#F4837F", 500: "#E45D58", 600: "#C53F3B" },
      },
      fontFamily: {
        sans: ["var(--font-body)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "Georgia", "serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.55)",
        glow: "0 0 0 1px rgba(233,168,61,0.35), 0 0 24px -6px rgba(233,168,61,0.35)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.45s cubic-bezier(0.22,1,0.36,1) both",
        shimmer: "shimmer 2.2s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
