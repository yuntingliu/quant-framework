import { mkdir, open, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { randomUUID } from 'node:crypto'

export async function writeJson(path, value) {
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600 })
    await rename(temporary, path)
  } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error }) }
}

export async function openState(directory, initial) {
  await mkdir(directory, { recursive: true, mode: 0o700 })
  const lock = resolve(directory, 'host.lock')
  let handle
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      handle = await open(lock, 'wx', 0o600)
      await handle.writeFile(String(process.pid))
      break
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      const owner = Number(await readFile(lock, 'utf8'))
      if (!Number.isSafeInteger(owner) || owner <= 0) throw new Error('Invalid local Host lock. Inspect host.lock before restarting.')
      try { process.kill(owner, 0) } catch (probe) {
        if (probe.code === 'ESRCH') { await unlink(lock); continue }
        throw probe
      }
      throw new Error('A local Host is already using this data directory.')
    }
  }
  if (!handle) throw new Error('Could not acquire the local Host lock.')
  const close = async () => { await handle.close(); await unlink(lock) }
  try {
    const path = resolve(directory, 'state.json')
    let state
    try { state = JSON.parse(await readFile(path, 'utf8')) }
    catch (error) { if (error.code !== 'ENOENT') throw error; state = initial }
    if (state.schema !== 'conexus.local-host.v1' || !Array.isArray(state.runs) || !Array.isArray(state.nodes)) {
      throw new Error('Unrecognized local Host state. Existing data has been preserved.')
    }
    // All mutations are serialized by the Host. Capture each snapshot before awaiting I/O.
    let saving = Promise.resolve()
    return { state, save: () => {
      const snapshot = structuredClone(state)
      saving = saving.then(() => writeJson(path, snapshot))
      return saving
    }, close: async () => { try { await saving } finally { await close() } } }
  } catch (error) { await close(); throw error }
}
