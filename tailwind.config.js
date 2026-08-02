/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './app/web/templates/**/*.html',
    './app/web/templates/**/*.jinja',
    './app/web/**/*.py',
    './app/web/static/js/**/*.js',
  ],
  /* Safelist for Alpine.js dynamic classes that Tailwind can't detect at build time */
  safelist: [
    /* Double-click delete button states */
    'bg-danger-500',
    'text-danger-400',
    'border-danger-500',
    'border-danger-500/50',
    'shadow-glow-danger',
    'hover:bg-danger-500',
    'hover:text-white',
    'hover:border-danger-500',
    'hover:shadow-glow-danger',
    /* Stat card glow effects */
    'group-hover:shadow-glow',
    'group-hover:shadow-glow-success',
    'group-hover:shadow-glow-warning',
    'group-hover:shadow-glow-danger',
    'group-hover:shadow-glow-info',
  ],
  theme: {
    extend: {
      colors: {
        /* ===========================================
         * LuxUPT Brand Colors
         * Generated from colorffy.com dark theme
         * =========================================== */

        /* Primary - Magenta/Pink accent */
        primary: {
          DEFAULT: '#ff00ea',
          50: '#fff0fe',
          100: '#ffe0fd',
          200: '#ff82f2',   /* a30 */
          300: '#ff68ef',   /* a20 */
          400: '#ff47ed',   /* a10 */
          500: '#ff00ea',   /* a0 - base */
          600: '#cc00bb',
          700: '#990088',
          800: '#660055',
          900: '#330022',
        },

        /* Surface - Neutral dark backgrounds */
        surface: {
          DEFAULT: '#121212',
          50: '#f5f5f5',
          100: '#e0e0e0',
          200: '#717171',   /* a40 */
          300: '#575757',   /* a30 */
          400: '#3f3f3f',   /* a20 */
          500: '#282828',   /* a10 */
          600: '#1e1e1e',
          700: '#181818',
          800: '#141414',
          900: '#121212',   /* a0 - darkest */
          950: '#0a0a0a',
        },

        /* Surface Tonal - Purple-tinted dark backgrounds */
        'surface-tonal': {
          DEFAULT: '#271924',
          50: '#f8f5f7',
          100: '#e8e0e6',
          200: '#7f767d',   /* a40 */
          300: '#685d65',   /* a30 */
          400: '#51454e',   /* a20 */
          500: '#3c2e39',   /* a10 */
          600: '#312631',
          700: '#2c212a',
          800: '#271924',   /* a0 */
          900: '#1a1018',
          950: '#0d080c',
        },

        /* Success - Green */
        success: {
          DEFAULT: '#22946e',
          50: '#f0fdf8',
          100: '#d1fae8',
          200: '#9ae8ce',   /* a20 - light */
          300: '#6bddb5',
          400: '#47d5a6',   /* a10 - medium */
          500: '#22946e',   /* a0 - base */
          600: '#1a7557',
          700: '#135640',
          800: '#0d3729',
          900: '#061812',
        },

        /* Warning - Amber/Orange */
        warning: {
          DEFAULT: '#a87a2a',
          50: '#fefbf3',
          100: '#fdf3e0',
          200: '#ecd7b2',   /* a20 - light */
          300: '#e2c28a',
          400: '#d7ac61',   /* a10 - medium */
          500: '#a87a2a',   /* a0 - base */
          600: '#866220',
          700: '#644916',
          800: '#42300c',
          900: '#201802',
        },

        /* Danger - Red */
        danger: {
          DEFAULT: '#9c2121',
          50: '#fef5f5',
          100: '#fde0e0',
          200: '#eb9e9e',   /* a20 - light */
          300: '#e27474',
          400: '#d94a4a',   /* a10 - medium */
          500: '#9c2121',   /* a0 - base */
          600: '#7d1a1a',
          700: '#5e1313',
          800: '#3f0c0c',
          900: '#1f0505',
        },

        /* Info - Blue */
        info: {
          DEFAULT: '#21498a',
          50: '#f5f8fc',
          100: '#e0ebf7',
          200: '#92b2e5',   /* a20 - light */
          300: '#6995db',
          400: '#4077d1',   /* a10 - medium */
          500: '#21498a',   /* a0 - base */
          600: '#1a3a6e',
          700: '#132b52',
          800: '#0c1c36',
          900: '#050d1a',
        },
      },

      /* Box shadows with primary glow */
      boxShadow: {
        'glow-sm': '0 2px 10px rgba(255, 0, 234, 0.10)',
        'glow': '0 4px 20px rgba(255, 0, 234, 0.15)',
        'glow-md': '0 8px 30px rgba(255, 0, 234, 0.20)',
        'glow-lg': '0 8px 40px rgba(255, 0, 234, 0.25)',
        'glow-xl': '0 12px 50px rgba(255, 0, 234, 0.35)',
        'glow-focus': '0 0 0 3px rgba(255, 0, 234, 0.30)',
        /* Semantic glows */
        'glow-success': '0 4px 20px rgba(34, 148, 110, 0.30)',
        'glow-warning': '0 4px 20px rgba(168, 122, 42, 0.30)',
        'glow-danger': '0 4px 20px rgba(156, 33, 33, 0.30)',
        'glow-info': '0 4px 20px rgba(33, 73, 138, 0.30)',
      },

      /* Background gradients */
      backgroundImage: {
        'surface-gradient': 'linear-gradient(135deg, #121212 0%, #0a0a0a 50%, #121212 100%)',
        'surface-tonal-gradient': 'linear-gradient(135deg, #271924 0%, #1a1018 50%, #271924 100%)',
        'primary-gradient': 'linear-gradient(135deg, #ff00ea 0%, #ff68ef 100%)',
      },

      /* Typography */
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        mono: [
          'JetBrains Mono',
          'Fira Code',
          'Monaco',
          'Consolas',
          'monospace',
        ],
      },

      /* Animations */
      animation: {
        'spin-slow': 'spin 3s linear infinite',
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow-pulse': 'glow-pulse 2s ease-in-out infinite',
      },
      keyframes: {
        'glow-pulse': {
          '0%, 100%': { boxShadow: '0 0 20px rgba(255, 0, 234, 0.2)' },
          '50%': { boxShadow: '0 0 30px rgba(255, 0, 234, 0.4)' },
        },
      },

      /* Border radius */
      borderRadius: {
        'xl': '1rem',
        '2xl': '1.5rem',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ],
}
