/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Matches the existing app's toast palette (App.jsx) — kept as the
        // single source of truth so every new page stays visually consistent
        // with the toasts the user already ships.
        ink: '#333333',
        'ink-soft': '#757575',
        line: '#E0E0E0',
        good: '#4CAF50',
        warn: '#FFC107',
        bad: '#E53935',
        info: '#7C4DFF',
        surface: '#FFFFFF',
        canvas: '#F5F6F8',
      },
      boxShadow: {
        card: '0 4px 12px rgba(0,0,0,0.1)',
      },
      borderRadius: {
        card: '8px',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'system-ui',
          'sans-serif',
        ],
        mono: ['ui-monospace', '"SF Mono"', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};
