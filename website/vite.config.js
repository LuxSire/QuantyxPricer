import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
  // Base path for GitHub Pages when repository is served at
  // https://LuxSire.github.io/QuantyxPricer
  base: '/QuantyxPricer/',
  plugins: [react()],
  server: {
    port: 5173,
    fs: {
      // Allow serving files from the project root (so /assets is accessible)
      allow: [resolve(__dirname, '..')],
    },
  },
})
