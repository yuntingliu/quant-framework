// Resolves the pinned Conexus core that `python scripts/build_conexus_runtime.py`
// produces under build/conexus. Examples never import vendor/conexus directly:
// that tree is a checksum-verified snapshot, and only the built output carries
// its installed dependencies.
import { resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { existsSync } from 'node:fs'

export const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')

const entry = resolve(repositoryRoot, 'build/conexus/apps/local-host/src/index.mjs')

export const BUILD_HINT =
  'Build the pinned Conexus runtime first: python scripts/build_conexus_runtime.py'

export function runtimeIsBuilt() {
  return existsSync(entry) && existsSync(resolve(repositoryRoot, 'build/conexus/node_modules/.package-lock.json'))
}

export async function loadConexus() {
  if (!runtimeIsBuilt()) throw new Error(BUILD_HINT)
  return import(pathToFileURL(entry).href)
}
