/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { vendorChunk } from './src/vendorChunk'

const apiProxyTarget = process.env.MORROW_API_PROXY ?? 'http://127.0.0.1:8787'

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../src/morrow/gui_static',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          return vendorChunk(id)
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/v1': {
        target: apiProxyTarget,
        // Core 校验 Host 端口必须等于监听端口；不改写 Host 会被 403 拒绝。
        changeOrigin: true,
        // GUI 模式的 Core 还要求 Origin/Referer 与 Host 同源；把两者一并改写
        // 成目标地址，浏览器侧仍保持 5173 同源，服务端同源检查不受影响。
        configure: (proxy) => {
          const rewrite = (proxyReq: { setHeader: (name: string, value: string) => void }) => {
            const target = new URL(apiProxyTarget)
            proxyReq.setHeader('origin', target.origin)
            proxyReq.setHeader('referer', `${target.origin}/`)
          }
          proxy.on('proxyReq', rewrite)
          proxy.on('proxyReqWs', rewrite)
        },
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./vitest.setup.ts'],
  },
})
