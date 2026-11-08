/** @type {import('tailwindcss').Config} */
// Colours are read from the theme variables in styles.css, so Light and Dark share every class.
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "var(--background)",
        panel: "var(--surface)",
        line: "var(--border)",
        mint: "var(--accent)",
        zinc: { 50: "var(--z50)", 100: "var(--z100)", 200: "var(--z200)", 300: "var(--z300)", 400: "var(--z400)", 500: "var(--z500)", 600: "var(--z600)", 700: "var(--z700)", 800: "var(--z800)", 900: "var(--z900)" }
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI Variable", "Segoe UI", "sans-serif"]
      },
      boxShadow: { glow: "0 2px 8px rgba(0,0,0,.03)" }
    }
  },
  plugins: []
};
