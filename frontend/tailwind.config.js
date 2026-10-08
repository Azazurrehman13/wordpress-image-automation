/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#E8ECEF",
        surface: "#FFFFFF",
        ink: "#15212B",
        muted: "#596874",
        line: "#C9D2D9",
        accent: { DEFAULT: "#0E5A63", soft: "#D6E9EA", dark: "#0A444B" },
        best: "#E3A008",
        danger: "#B3261E",
        ok: "#1F7A4D",
      },
      fontFamily: {
        sans: ['"Segoe UI Variable Text"', '"Segoe UI"', "system-ui", "sans-serif"],
        display: ['"Segoe UI Variable Display"', '"Segoe UI Semibold"', '"Segoe UI"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
