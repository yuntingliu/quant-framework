// Private worker protocol. This file is never imported by a live Agent adapter.
import { parentPort, workerData } from 'node:worker_threads'
import * as fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

const MAX_BYTES = 4 * 1024 * 1024
const MAX_NODES = 32768
const PROFILE = /^[a-z0-9][a-z0-9_-]{0,63}$/
const OPAQUE = /^[0-9a-f]{32}$/
const APPLICATION_ID = 0x414c4346
class Failure { constructor(code) { this.code = code } }
function fail(code) { throw new Failure(code) }
// Python ntpath.normcase uses Windows invariant NLS, not ICU linguistic
// lowercasing. These compatibility exclusions were checked against
// LCMapStringEx for all Unicode scalars. The regression repeats every Node
// case-changing scalar against Python; do not change the existing root binding.
const WINDOWS_CASE_EXCLUSIONS = [
  [0x1c5, 0x1c5], [0x1c8, 0x1c8], [0x1cb, 0x1cb], [0x1f2, 0x1f2],
  [0x37f, 0x37f], [0x524, 0x524], [0x526, 0x526], [0x528, 0x528],
  [0x52a, 0x52a], [0x52c, 0x52c], [0x52e, 0x52e], [0x10c7, 0x10c7],
  [0x10cd, 0x10cd], [0x13a0, 0x13f5], [0x1c89, 0x1c89], [0x1c90, 0x1cba],
  [0x1cbd, 0x1cbf], [0x2c2f, 0x2c2f], [0x2c70, 0x2c70], [0x2c7e, 0x2c7f],
  [0x2ceb, 0x2ceb], [0x2ced, 0x2ced], [0x2cf2, 0x2cf2], [0xa660, 0xa660],
  [0xa698, 0xa698], [0xa69a, 0xa69a], [0xa78d, 0xa78d], [0xa790, 0xa790],
  [0xa792, 0xa792], [0xa796, 0xa796], [0xa798, 0xa798], [0xa79a, 0xa79a],
  [0xa79c, 0xa79c], [0xa79e, 0xa79e], [0xa7a0, 0xa7a0], [0xa7a2, 0xa7a2],
  [0xa7a4, 0xa7a4], [0xa7a6, 0xa7a6], [0xa7a8, 0xa7a8], [0xa7aa, 0xa7ae],
  [0xa7b0, 0xa7b4], [0xa7b6, 0xa7b6], [0xa7b8, 0xa7b8], [0xa7ba, 0xa7ba],
  [0xa7bc, 0xa7bc], [0xa7be, 0xa7be], [0xa7c0, 0xa7c0], [0xa7c2, 0xa7c2],
  [0xa7c4, 0xa7c7], [0xa7c9, 0xa7c9], [0xa7cb, 0xa7cc], [0xa7ce, 0xa7ce],
  [0xa7d0, 0xa7d0], [0xa7d2, 0xa7d2], [0xa7d4, 0xa7d4], [0xa7d6, 0xa7d6],
  [0xa7d8, 0xa7d8], [0xa7da, 0xa7da], [0xa7dc, 0xa7dc], [0xa7f5, 0xa7f5],
  [0x104b0, 0x104d3], [0x10570, 0x1057a], [0x1057c, 0x1058a], [0x1058c, 0x10592],
  [0x10594, 0x10595], [0x10c80, 0x10cb2], [0x10d50, 0x10d65], [0x118a0, 0x118bf],
  [0x16e40, 0x16e5f], [0x16ea0, 0x16eb8], [0x1e900, 0x1e921],
]
function normalized(value) {
  if (process.platform !== 'win32') return value
  return Array.from(value.replaceAll('/', '\\'), character => {
    const lower = character.toLowerCase()
    const code = character.codePointAt(0)
    if (lower.toUpperCase() !== character.toUpperCase()
      || WINDOWS_CASE_EXCLUSIONS.some(([first, last]) => code >= first && code <= last)) return character
    return lower
  }).join('')
}

// Pure protocol helper: the worker supplies an already canonical root.
export function configurationRootKey(canonical) {
  return createHash('sha256').update(normalized(canonical), 'utf8').digest('hex')
}
const sameFile = (one, two) => one.dev === two.dev && one.ino === two.ino
const privateMode = stat => process.platform === 'win32' || (stat.mode & 0o77n) === 0n

// Strict native JSON: duplicate keys, escaped aliases and oversized/deep values
// fail without echoing source text. Keep integer token types until validation;
// unsafe 64-bit integer metadata remains BigInt rather than silently rounding.
function parseNative(text, budget, initialDepth) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > MAX_BYTES) fail('STORE_CORRUPT')
  let offset = 0
  function whitespace() { while (/[ \t\r\n]/.test(text[offset] ?? '\0')) offset++ }
  function string() {
    const start = offset++
    while (offset < text.length) {
      if (text[offset] === '\\') { offset += 2; continue }
      if (text[offset++] === '"') {
        let value
        try { value = JSON.parse(text.slice(start, offset)) } catch { fail('STORE_CORRUPT') }
        if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) fail('STORE_CORRUPT')
        return value
      }
    }
    fail('STORE_CORRUPT')
  }
  function value(depth) {
    if (++budget.nodes > MAX_NODES || depth > 32) fail('STORE_CORRUPT')
    whitespace()
    const char = text[offset]
    if (char === '"') return string()
    if (char === '{' || char === '[') {
      const object = char === '{', result = object ? Object.create(null) : []
      const close = object ? '}' : ']'
      offset++; whitespace()
      if (text[offset] === close) { offset++; return result }
      while (offset < text.length) {
        let key
        if (object) {
          if (++budget.nodes > MAX_NODES || text[offset] !== '"') fail('STORE_CORRUPT')
          key = string(); whitespace()
          if (Object.hasOwn(result, key) || text[offset++] !== ':') fail('STORE_CORRUPT')
        }
        const child = value(depth + 1)
        if (object) result[key] = child
        else result.push(child)
        whitespace()
        const separator = text[offset++]
        if (separator === close) return result
        if (separator !== ',') fail('STORE_CORRUPT')
        whitespace()
      }
      fail('STORE_CORRUPT')
    }
    for (const [token, result] of [['true', true], ['false', false], ['null', null]]) {
      if (text.startsWith(token, offset)) { offset += token.length; return result }
    }
    const token = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(offset))?.[0]
    if (!token) fail('STORE_CORRUPT')
    offset += token.length
    if (/[.eE]/.test(token)) {
      const number = Number(token)
      if (!Number.isFinite(number)) fail('STORE_CORRUPT')
      return number
    }
    if (token.length > 21) fail('STORE_CORRUPT')
    const integer = BigInt(token)
    if (integer > 18446744073709551615n || integer < -18446744073709551615n) fail('STORE_CORRUPT')
    return integer
  }
  const result = value(initialDepth)
  whitespace()
  if (offset !== text.length) fail('STORE_CORRUPT')
  return result
}

function nativeNumbers(value) {
  if (typeof value === 'bigint') return value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= BigInt(Number.MIN_SAFE_INTEGER) ? Number(value) : value
  if (value && typeof value === 'object') {
    for (const key of Object.keys(value)) value[key] = nativeNumbers(value[key])
  }
  return value
}
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const identifier = value => typeof value === 'string' && PROFILE.exec(value)?.[0] === value

function validate(document, choice, service) {
  if (!record(document) || !record(choice) || typeof choice.kind !== 'string') fail('STORE_CORRUPT')
  if (document.version !== 1n && !(service === 'search' && document.version === 2n)) fail('STORE_CORRUPT')
  let active, ids
  if (service === 'search' && document.version === 1n) {
    if (!['brave', 'openrouter', 'off'].includes(document.provider)) fail('STORE_CORRUPT')
    ids = new Set(['default']); active = 'default'
  } else {
    if (!Array.isArray(document.profiles) || !Object.hasOwn(document, 'active_id')) fail('STORE_CORRUPT')
    ids = new Set()
    for (const profile of document.profiles) {
      if (!record(profile) || !identifier(profile.id) || ids.has(profile.id)) fail('STORE_CORRUPT')
      ids.add(profile.id)
    }
    active = document.active_id
    if (active !== null && (!identifier(active) || !ids.has(active))) fail('STORE_CORRUPT')
  }
  if (choice.kind === 'profile') {
    if (Object.keys(choice).sort().join(',') !== 'id,kind' || !identifier(choice.id) || !ids.has(choice.id) || choice.id !== active) fail('STORE_CORRUPT')
  } else if (!['environment', 'none'].includes(choice.kind) || Object.keys(choice).length !== 1 || active !== null) fail('STORE_CORRUPT')
}

function checkedLocation(root) {
  const canonical = fs.realpathSync.native(path.resolve(root))
  const parent = path.join(canonical, 'app', 'secrets')
  const directories = [path.join(canonical, 'app'), parent].map(directory => {
    const stat = fs.lstatSync(directory, { bigint: true })
    if (!stat.isDirectory() || stat.isSymbolicLink() || normalized(fs.realpathSync.native(directory)) !== normalized(directory)) fail('UNSAFE_STORAGE')
    return { directory, stat }
  })
  if (!privateMode(directories[1].stat)) fail('UNSAFE_STORAGE')
  const filename = path.join(parent, 'configuration-store.sqlite3')
  function check() {
    for (const item of directories) {
      const current = fs.lstatSync(item.directory, { bigint: true })
      if (current.isSymbolicLink() || !current.isDirectory() || !sameFile(current, item.stat)) fail('UNSAFE_STORAGE')
      if (item.directory === parent && !privateMode(current)) fail('UNSAFE_STORAGE')
    }
    const stat = fs.lstatSync(filename, { bigint: true })
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1n || !privateMode(stat)) fail('UNSAFE_STORAGE')
    for (const suffix of ['-wal', '-shm', '-journal']) {
      let sidecar
      try { sidecar = fs.lstatSync(filename + suffix, { bigint: true }) } catch (error) {
        if (Object.getOwnPropertyDescriptor(error, 'code')?.value === 'ENOENT') continue
        throw error
      }
      if (suffix !== '-journal') fail('UNSUPPORTED_STORAGE')
      if (!sidecar.isFile() || sidecar.isSymbolicLink() || sidecar.nlink !== 1n || !privateMode(sidecar)) fail('UNSAFE_STORAGE')
    }
    return stat
  }
  return { filename, check, key: configurationRootKey(canonical) }
}

function header(fd) {
  const bytes = Buffer.alloc(100)
  if (fs.readSync(fd, bytes, 0, 100, 0) !== 100 || bytes.subarray(0, 16).toString('latin1') !== 'SQLite format 3\0'
    || bytes.readUInt32BE(68) !== APPLICATION_ID) fail('STORE_CORRUPT')
  if (bytes.readUInt32BE(60) !== 1) fail('UNSUPPORTED_PROTOCOL')
  if (bytes[18] !== 1 || bytes[19] !== 1) fail('UNSUPPORTED_STORAGE')
}

function read(DatabaseSync) {
  const { root, deadline, duration, installation } = workerData
  const location = checkedLocation(root)
  const before = location.check()
  let fd, database
  try {
    fd = fs.openSync(location.filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0))
    if (!sameFile(before, fs.fstatSync(fd, { bigint: true }))) fail('UNSAFE_STORAGE')
    header(fd)
    database = new DatabaseSync(location.filename, { readOnly: true, timeout: Math.max(0, Math.min(250, deadline - Date.now())), allowExtension: false, enableDoubleQuotedStringLiterals: false })
    function execute(sql, parameters = []) {
      const remaining = Math.min(duration, deadline - Date.now())
      if (remaining <= 0) fail('STORE_TIMEOUT')
      database.exec(`PRAGMA busy_timeout=${Math.min(250, remaining)}`)
      const statement = database.prepare(sql)
      statement.setReadBigInts(true)
      return statement.all(...parameters)
    }
    database.exec('PRAGMA trusted_schema=OFF; PRAGMA query_only=ON; BEGIN')
    if (execute('PRAGMA journal_mode')[0]?.journal_mode !== 'delete') fail('UNSUPPORTED_STORAGE')
    const tables = execute("SELECT name, type FROM sqlite_schema WHERE name IN ('store_metadata','generations')")
    if (tables.length !== 2 || tables.some(row => row.type !== 'table')) fail('STORE_CORRUPT')
    for (const [table, names] of [
      ['store_metadata', 'current_revision,installation_id,protocol_version,root_key,singleton'],
      ['generations', 'models,ordinal,parent_revision,revision,rqdata,search,selections'],
    ]) {
      if (execute(`PRAGMA table_info(${table})`).map(row => row.name).sort().join(',') !== names) fail('STORE_CORRUPT')
    }
    const metadata = execute('SELECT singleton, protocol_version, installation_id, root_key, current_revision FROM store_metadata LIMIT 2')
    if (metadata.length !== 1 || metadata[0].singleton !== 1n) fail('STORE_CORRUPT')
    const meta = metadata[0]
    if (meta.protocol_version !== 1n) fail('UNSUPPORTED_PROTOCOL')
    if (typeof meta.installation_id !== 'string' || meta.installation_id.length !== 32 || !OPAQUE.test(meta.installation_id)
      || typeof meta.current_revision !== 'string' || meta.current_revision.length !== 32 || !OPAQUE.test(meta.current_revision)) fail('STORE_CORRUPT')
    if (meta.root_key !== location.key || (installation !== null && installation !== meta.installation_id)) fail('INSTALLATION_MISMATCH')
    // Never select rqdata: no RQData credentials cross the SQLite/JS boundary.
    const rows = execute('SELECT models, search, selections FROM generations WHERE revision=? AND ordinal=(SELECT max(ordinal) FROM generations) '
      + `AND length(CAST(models AS BLOB))<=${MAX_BYTES} AND length(CAST(search AS BLOB))<=${MAX_BYTES} AND length(CAST(selections AS BLOB))<=${MAX_BYTES} LIMIT 2`, [meta.current_revision])
    if (rows.length !== 1) fail('STORE_CORRUPT')
    const row = rows[0]
    const texts = [row.models, row.search, row.selections]
    if (texts.some(text => typeof text !== 'string') || texts.reduce((size, text) => size + Buffer.byteLength(text), 0) > MAX_BYTES) fail('STORE_CORRUPT')
    const budget = { nodes: 5 } // Native wrapper/document mapping and service keys.
    const [models, search, selections] = texts.map((text, index) => parseNative(text, budget, index === 2 ? 1 : 2))
    if (!record(selections) || Object.keys(selections).sort().join(',') !== 'models,rqdata,search') fail('STORE_CORRUPT')
    validate(models, selections.models, 'models')
    validate(search, selections.search, 'search')
    const rq = selections.rqdata
    if (!record(rq) || (rq.kind === 'profile' ? Object.keys(rq).sort().join(',') !== 'id,kind' || !identifier(rq.id)
      : !['environment', 'none'].includes(rq.kind) || Object.keys(rq).length !== 1)) fail('STORE_CORRUPT')
    execute('COMMIT')
    if (!sameFile(before, location.check()) || !sameFile(before, fs.fstatSync(fd, { bigint: true }))) fail('UNSAFE_STORAGE')
    header(fd)
    return { protocol_version: 1, installation_id: meta.installation_id, revision: meta.current_revision,
      models: nativeNumbers(models), search: nativeNumbers(search),
      selections: nativeNumbers({ models: selections.models, search: selections.search }) }
  } finally {
    try { database?.close() } finally { if (fd !== undefined) fs.closeSync(fd) }
  }
}

function errorCode(error) {
  try {
    if (Object.getPrototypeOf(error) === Failure.prototype) return error.code
    const code = Object.getOwnPropertyDescriptor(error, 'code')?.value
    if (code === 'ENOENT') return 'STORE_MISSING'
    if (code === 'ERR_UNKNOWN_BUILTIN_MODULE') return 'UNSUPPORTED_RUNTIME'
    const number = Object.getOwnPropertyDescriptor(error, 'errcode')?.value
    if (number === 264 || number === 776) return 'RECOVERY_REQUIRED'
    if (typeof number === 'number' && [5, 6].includes(number & 255)) return 'STORE_BUSY'
    if (typeof number === 'number' && [1, 11, 17, 24, 26].includes(number & 255)) return 'STORE_CORRUPT'
  } catch { /* Never inspect error messages, stacks, or underlying causes. */ }
  return 'STORE_UNAVAILABLE'
}

if (parentPort) {
  let response
  try {
    const [major, minor] = process.versions.node.split('.').map(Number)
    if (major !== 22 || minor < 18) fail('UNSUPPORTED_RUNTIME')
    const { DatabaseSync } = await import('node:sqlite')
    response = { ok: true, snapshot: read(DatabaseSync) }
  } catch (error) { response = { ok: false, code: errorCode(error) } }
  try { parentPort.postMessage(response) }
  finally { parentPort.close() }
}
