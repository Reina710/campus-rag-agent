import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物直接落到 Flask 的 static 目录，运行时不额外起前端服务。
// base 必须是 /static/，否则 Flask 挂载路径与资源路径对不上。
export default defineConfig({
  plugins: [react()],
  base: '/static/',
  // 开发时 npm run dev：接口请求转发给 Flask，避免跨域，也不用改前端代码里的路径
  server: {
    proxy: {
      '/ask': 'http://127.0.0.1:8000',
      '/ask_stream': 'http://127.0.0.1:8000',
      '/clear': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
  build: {
    // 本地构建落到 ../static（Flask 直接托管）；
    // Docker 里为了让产物落在构建阶段的独立目录，用 OUT_DIR 覆盖。
    outDir: process.env.OUT_DIR || '../static',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
})
