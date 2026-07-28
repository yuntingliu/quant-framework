import path from "path"
import type { UserConfig } from "vite"

const apiTarget = process.env.ALPHALAB_API_ORIGIN ?? "http://127.0.0.1:8000"

export function sharedConfig(): UserConfig {
  return {
    base: "./",
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    build: {
      target: "chrome128",
      modulePreload: false,
      chunkSizeWarningLimit: 600,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes("node_modules")) return undefined
            const pkg = id.replace(/\\/g, "/").split("node_modules/")[1] ?? ""
            if (pkg.startsWith("dockview/")) return "vendor-dockview"
            if (pkg.startsWith("recharts/") || pkg.startsWith("d3-")) return "vendor-charts"
            if (pkg.startsWith("@tanstack/")) return "vendor-query"
            if (
              pkg.startsWith("@radix-ui/")
              || pkg.startsWith("cmdk/")
              || pkg.startsWith("sonner/")
            ) return "vendor-ui"
            if (pkg.startsWith("framer-motion/")) return "vendor-motion"
            if (pkg.startsWith("lucide-react/")) return "vendor-icons"
            if (
              pkg.startsWith("react/")
              || pkg.startsWith("react-dom/")
              || pkg.startsWith("scheduler/")
            ) return "vendor-react"
            return undefined
          },
        },
      },
    },
  }
}
