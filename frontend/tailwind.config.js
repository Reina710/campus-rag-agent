/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // 深空底 + 一个青紫强调色。只用一套色，避免赛博朋克那种花。
        ink: '#05060a',
        panel: 'rgba(255,255,255,0.03)',
        line: 'rgba(255,255,255,0.08)',
        cyan: '#4cc9f0',
        violet: '#7b61ff',
        dim: '#7c8798',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      keyframes: {
        rise: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        breathe: {
          '0%,100%': { opacity: '1', boxShadow: '0 0 0 0 rgba(76,201,240,0.45)' },
          '50%': { opacity: '.7', boxShadow: '0 0 0 5px rgba(76,201,240,0)' },
        },
        caret: {
          '0%,100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        sweep: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        rise: 'rise .35s cubic-bezier(.22,1,.36,1) both',
        breathe: 'breathe 2.4s ease-in-out infinite',
        caret: 'caret 1s step-end infinite',
        sweep: 'sweep 2.6s linear infinite',
      },
    },
  },
  plugins: [],
}
