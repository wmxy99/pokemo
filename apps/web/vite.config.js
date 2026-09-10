import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// PokéLab AI Web
// - 构建（build）：base = /app/ —— 与 FastAPI 的 app.mount("/app", dist) 同源部署一致
// - 开发（serve）：base = /，Vite dev server 直接把 /api 代理到后端 8000
//   自部署到根路径时可用 VITE_BASE=/ 覆盖。
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: process.env.VITE_BASE || (command === 'build' ? '/app/' : '/'),
  build: {
    // 受限环境（沙箱/无回收站）下禁止清空 outDir，避免 safe-delete 失败
    emptyOutDir: false,
    // 输出目录可覆盖（Windows 上 dist 被进程占用时用 VITE_OUTDIR=dist2）
    outDir: process.env.VITE_OUTDIR || 'dist',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
}))
