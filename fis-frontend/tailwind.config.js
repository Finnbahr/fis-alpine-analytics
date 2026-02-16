/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Primary accent color (cyan/teal)
        primary: {
          50: '#ecfeff',
          100: '#cffafe',
          200: '#a5f3fc',
          300: '#67e8f9',
          400: '#22d3ee', // Main accent
          500: '#06b6d4',
          600: '#0891b2',
          700: '#0e7490',
          800: '#155e75',
          900: '#164e63',
        },
        // Positive indicator (emerald green)
        positive: {
          400: '#34d399',
          500: '#10b981',
          600: '#059669',
        },
        // Negative indicator (red)
        negative: {
          400: '#f87171',
          500: '#ef4444',
          600: '#dc2626',
        },
      },
      backgroundColor: {
        // Dark theme backgrounds
        'dark-base': '#030712',      // gray-950 - deepest background
        'dark-elevated': '#111827',  // gray-900 - cards/panels
        'dark-surface': '#1f2937',   // gray-800 - elevated surfaces
      },
      borderColor: {
        'dark-border': '#374151',    // gray-700 - borders
        'dark-border-light': '#4b5563', // gray-600 - lighter borders
      },
    },
  },
  plugins: [],
}
