import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

import { sharedConfig } from "./vite.shared"

export default defineConfig({
  ...sharedConfig(),
  plugins: [react()],
})
