import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import renderer from 'vite-plugin-electron-renderer'
import path from 'path'

const apiTarget = process.env.ALPHALAB_API_ORIGIN ?? 'http://127.0.0.1:8000'
const isWebOnly = process.env.WEB_ONLY === '1'

const electronPlugins = isWebOnly
  ? []
  : [
      electron([
        {
          entry: 'electron/main.ts',
          vite: {
            build: {
              outDir: 'dist-electron',
              rollupOptions: {
                external: ['electron'],
              },
            },
          },
          // Windows fix: inject module resolution patch before main process
          onstart(args) {
            args.startup(['--require', './electron/electron-module-fix.cjs', '.'])
          },
        },
        {
          // Preload script: reloads renderer on change during dev
          entry: 'electron/preload.ts',
          onstart(args) {
            args.reload()
          },
          vite: {
            build: {
              outDir: 'dist-electron',
              rollupOptions: {
                external: ['electron'],
                output: {
                  format: 'cjs',
                  entryFileNames: 'preload.cjs',
                },
              },
            },
          },
        },
      ]),
      renderer(),
    ]

export default defineConfig({
  base: './',
  plugins: [react(), ...electronPlugins],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  preview: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  worker: {
    format: 'es',
  },
  build: {
    target: 'chrome128',
    modulePreload: false,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          const pkg = id.replace(/\\/g, '/').split('node_modules/')[1] ?? ''
          if (pkg.startsWith('dockview/')) return 'vendor-dockview'
          if (pkg.startsWith('recharts/') || pkg.startsWith('d3-')) return 'vendor-charts'
          if (pkg.startsWith('klinecharts/') || pkg.startsWith('lightweight-charts/')) return 'vendor-market-charts'
          if (pkg.startsWith('@tanstack/')) return 'vendor-query'
          if (pkg.startsWith('@radix-ui/') || pkg.startsWith('cmdk/') || pkg.startsWith('sonner/')) return 'vendor-ui'
          if (pkg.startsWith('framer-motion/')) return 'vendor-motion'
          if (pkg.startsWith('lucide-react/')) return 'vendor-icons'
          if (pkg.startsWith('react/') || pkg.startsWith('react-dom/') || pkg.startsWith('scheduler/')) return 'vendor-react'
          return undefined
        },
      },
    },
  },
})
