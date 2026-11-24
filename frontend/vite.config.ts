import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
const proxy = {
  '/api': apiTarget,
  '/assets/asset_': apiTarget,
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
