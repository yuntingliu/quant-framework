import path from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import electron from 'vite-plugin-electron'
import renderer from 'vite-plugin-electron-renderer'

import { sharedConfig } from '../vite.shared'

const frontendRoot = path.resolve(__dirname, '..')

export default defineConfig({
  ...sharedConfig(),
  root: frontendRoot,
  plugins: [
    react(),
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
        onstart(args) {
          args.startup([
            '--require',
            path.join(frontendRoot, 'electron/electron-module-fix.cjs'),
            frontendRoot,
          ])
        },
      },
      {
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
  ],
})
