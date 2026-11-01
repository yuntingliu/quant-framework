import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'

const require = createRequire(import.meta.url)

test('patched matcher supports legacy CommonJS and modern ESM consumers', async () => {
  const legacy = require('minimatch')
  const modern = await import('minimatch')
  assert.equal(legacy('src/main.ts', '**/*.ts'), true)
  assert.equal(legacy('src/main.js', '**/*.ts'), false)
  assert.equal(modern.minimatch('src/main.ts', '**/*.ts'), true)
  assert.equal(new modern.Minimatch('**/*.ts').match('src/main.ts'), true)
  assert.deepEqual(legacy.match(['a.ts', 'a.js'], '*.ts'), ['a.ts'])

  // Resolve from actual consumers, so a broken nested override cannot silently
  // pass just because importing the root dependency works.
  for (const consumer of [
    '@electron/asar', 'dir-compare', 'glob', 'filelist',
    'eslint', '@eslint/config-array', '@eslint/eslintrc',
    'vscode-languageclient', '@electron/universal', 'app-builder-lib',
  ]) {
    const fromConsumer = createRequire(require.resolve(consumer))
    const matcher = fromConsumer('minimatch')
    assert.equal(matcher('src/main.ts', '**/*.ts'), true, consumer)
    assert.equal(new matcher.Minimatch('**/*.ts').match('src/main.js'), false, consumer)
  }
})

test('ASAR discovery, archive extraction and native-file unpacking work', async (t) => {
  const dir = await mkdtemp(path.join(tmpdir(), 'alphalab-asar-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const source = path.join(dir, 'source')
  await mkdir(path.join(source, 'nested'), { recursive: true })
  await writeFile(path.join(source, 'nested', 'main.js'), 'export const value = 42')
  await writeFile(path.join(source, 'addon.node'), 'native fixture')
  await writeFile(path.join(source, '.config'), 'hidden fixture')
  const glob = require('glob')
  assert.deepEqual(glob.sync('**/*.js', { cwd: source }), ['nested/main.js'])
  assert.deepEqual(glob.sync('nested/@(main|other).js', { cwd: source }), ['nested/main.js'])
  assert.equal(glob.sync('*', { cwd: source }).includes('.config'), false)
  assert.equal(glob.sync('*', { cwd: source, dot: true }).includes('.config'), true)
  const asar = require('@electron/asar')
  const archive = path.join(dir, 'app.asar')
  await asar.createPackageWithOptions(source, archive, { unpack: '*.node' })
  assert.equal(asar.extractFile(archive, path.join('nested', 'main.js')).toString(), 'export const value = 42')
  assert.equal(asar.extractFile(archive, '.config').toString(), 'hidden fixture')
  assert.equal(asar.statFile(archive, 'addon.node').unpacked, true)
  assert.equal(await readFile(path.join(`${archive}.unpacked`, 'addon.node'), 'utf8'), 'native fixture')
})

test('directory comparison honors include and exclude globs', async (t) => {
  const dir = await mkdtemp(path.join(tmpdir(), 'alphalab-compare-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const left = path.join(dir, 'left')
  const right = path.join(dir, 'right')
  for (const side of [left, right]) {
    await mkdir(side)
    await writeFile(path.join(side, 'main.js'), 'same')
    await writeFile(path.join(side, 'skip.js'), side)
    await writeFile(path.join(side, 'notes.txt'), side)
  }
  const { compare } = require('dir-compare')
  assert.equal((await compare(left, right, { compareContent: true })).same, false)
  const filtered = await compare(left, right, {
    compareContent: true, includeFilter: '*.js', excludeFilter: 'skip.*',
  })
  assert.equal(filtered.same, true)
  assert.equal(filtered.equal, 1)
})
