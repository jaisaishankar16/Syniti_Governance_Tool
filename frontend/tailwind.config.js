/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        background: '#F8F9FB',
        border: '#E5E7EB',
        primary: {
          from: '#6D3FE0',
          to: '#3B82F6',
          DEFAULT: '#6D3FE0',
          hover: '#5B2FD0',
        },
        activePill: '#6D4AE8',
        success: {
          DEFAULT: '#22C55E',
          bg: '#DCFCE7',
          text: '#15803D',
        },
        warning: {
          DEFAULT: '#F59E0B',
          bg: '#FEF3C7',
          text: '#B45309',
        },
        danger: {
          DEFAULT: '#EF4444',
          bg: '#FEE2E2',
          text: '#B91C1C',
        },
        neutralChip: {
          bg: '#F3F4F6',
          text: '#4B5563',
        },
        heading: '#111827',
        secondary: '#6B7280',
      },
      borderRadius: {
        card: '12px',
      },
      fontFamily: {
        sans: ['InterVariable', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px -1px rgba(0, 0, 0, 0.05)',
      },
    },
  },
  plugins: [],
};

