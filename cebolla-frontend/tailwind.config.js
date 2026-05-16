/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Off-black scale (osadia-inspired)
        bg: {
          0:   '#08080A',   // page (almost-black)
          50:  '#0E0E12',   // panel
          100: '#16161B',   // surface
          150: '#1C1C22',   // hover
          200: '#26262E',   // border thin
          300: '#33333D',   // border bold
          400: '#52525F',   // muted
        },
        // Text
        fg: {
          400: '#6E6E7A',   // muted
          500: '#9B9BA8',   // secondary
          600: '#C6C6D0',   // body
          700: '#E8E8EE',   // primary
          800: '#FFFFFF',   // brightest
        },
        // Signal red (the single hot accent)
        signal: {
          50:  '#FFE5E5',
          100: '#FFB8B8',
          200: '#FF7777',
          300: '#FF3F3F',
          400: '#FF2A2A',   // primary
          500: '#E61E1E',
          600: '#B81616',
        },
        // Edge intensity (compatible w/ HR factor coding)
        edge: {
          'hot-3': '#FF2A2A',
          'hot-2': '#FF6B47',
          'hot-1': '#FFA84A',
          'neutral': '#6E6E7A',
          'cold-1': '#4FB1DD',
          'cold-2': '#3A8DBC',
        },
      },
      fontFamily: {
        display: ['Syne', 'Fraunces', 'Georgia', 'serif'],
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'Menlo', 'monospace'],
      },
      letterSpacing: {
        caps: '0.12em',
        wide2: '0.18em',
      },
      animation: {
        'pulse-slow': 'pulse 3s ease-in-out infinite',
        'scanline': 'scanline 6s linear infinite',
      },
      keyframes: {
        scanline: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
      },
    },
  },
  plugins: [],
}
