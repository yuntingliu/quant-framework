import { readFile, readdir } from 'node:fs/promises'
import { resolve, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(await readFile(resolve(root, 'open-core.manifest.json'), 'utf8'))
const names = new Map(await Promise.all(manifest.packages.map(async path => {
  const pkg = JSON.parse(await readFile(resolve(root, path, 'package.json'), 'utf8'))
  return [pkg.name, path]
})))
const violations = []
async function inspect(path) {
  for (const entry of await readdir(path, { withFileTypes: true })) {
    if (['node_modules', 'dist', 'server-dist'].includes(entry.name)) continue
    const absolute = resolve(path, entry.name)
    if (entry.isSymbolicLink()) { violations.push(`Export source contains a symlink: ${relative(root, absolute)}`); continue }
    if (entry.isDirectory()) { await inspect(absolute); continue }
    if (!/\.(?:[cm]?js|tsx?)$/.test(entry.name)) continue
    const source = await readFile(absolute, 'utf8')
    const specs = [...source.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|\bimport\s*)['"]([^'"]+)['"]/g)].map(match => match[1])
    for (const specifier of specs) {
      if (specifier.startsWith('@conexus/') && !names.has(specifier.split('/').slice(0, 2).join('/'))) {
        violations.push(`${relative(root, absolute)} imports private package ${specifier}`)
      }
      if (specifier.startsWith('.')) {
        const target = relative(root, resolve(dirname(absolute), specifier)).replaceAll('\\', '/')
        if (!manifest.packages.some(owner => target.startsWith(`${owner}/`))) violations.push(`${relative(root, absolute)} imports outside the export: ${specifier}`)
      }
    }
    if (/EnterpriseCredentialStore|conexus_account|billingPolicy|@supabase\/supabase-js|conexus\.enterprise-publication/.test(source)) {
      violations.push(`${relative(root, absolute)} contains a commercial dependency or contract`)
    }
  }
}
for (const path of manifest.packages) {
  const pkg = JSON.parse(await readFile(resolve(root, path, 'package.json'), 'utf8'))
  for (const [name, specifier] of Object.entries({ ...pkg.dependencies, ...pkg.devDependencies })) {
    if (name.startsWith('@conexus/') && !names.has(name)) violations.push(`${path} depends on private package ${name}`)
    if (specifier.startsWith('file:')) {
      const target = relative(root, resolve(root, path, specifier.slice(5))).replaceAll('\\', '/')
      if (!manifest.packages.includes(target)) violations.push(`${path} has a dependency outside the export: ${specifier}`)
    }
  }
  await inspect(resolve(root, path))
}
if (violations.length) { console.error(violations.join('\n')); process.exitCode = 1 }
else console.log(`Open core boundary passed: ${names.size} packages; no private package imports.`)
