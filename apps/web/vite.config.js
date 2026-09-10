import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发：/api/* → FastAPI(8000)（去掉 /api 前缀）
export default defineConfig({
  plugins: [react()],
  build: {
    // 沙箱/受限环境回收站不可用，禁止清空 outDir（残留旧 hash 资源无害）
    emptyOutDir: false,
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
})
