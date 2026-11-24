import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
const proxy = {
  '/api': apiTarget,
  '/assets': {
    target: apiTarget,
    bypass(req: { url?: string }) {
      const pathname = decodeURIComponent((req.url || '').split('?')[0])
      if (existsSync(resolve(process.cwd(), 'dist', pathname)) ||
          existsSync(resolve(process.cwd(), 'public', pathname))) return req.url
    },
  },
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy,
  },
  preview: {
    port: 4173,
    host: true,
    proxy,
  },
})
