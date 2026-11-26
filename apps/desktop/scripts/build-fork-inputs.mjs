// The fork stages its Python payload from a Git revision, then stamps that tree in place.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { parseArgs } from 'node:util'
import { fileDigest, treeDigest } from './prepared-packaging.mjs'
import { isMain } from './utils.mjs'

function payloadKey(source, env, versions) {
  const git = (...args) => execFileSync('git', args, { cwd: source, maxBuffer: 32 * 1024 * 1024 })
  // stage.py snapshots HEAD, but its Python builder and the frontend drivers execute from the checkout.
  const drivers = ['scripts', 'pm', 'hermes_platform', 'hermes_cli', 'agent', ':(top,glob)*.py',
    'apps/desktop/package.json', 'apps/desktop/scripts/build-fork-inputs.mjs']
  const untracked = git('ls-files', '-z', '--others', '--exclude-standard', '--', ...drivers)
    .toString().split('\0').filter(Boolean).map(name => [name, fileDigest(path.join(source, name))])
  const settings = Object.entries(env).filter(([name]) =>
    /^(HERMES_|UV_|VITE_|NODE_|NPM_CONFIG_|PNPM_|PYTHON|PIP_|CARGO_|RUST|CMAKE_|SOURCE_DATE_EPOCH$|PATH$|CC$|CXX$|CFLAGS$|LDFLAGS$)/i.test(name))
  return createHash('sha256')
    .update(JSON.stringify([1, source, process.platform, process.arch, versions, settings.sort(), untracked]))
    .update(git('rev-parse', 'HEAD')).update(git('diff', '--no-ext-diff', '--binary', 'HEAD', '--', ...drivers))
    .digest('hex')
}

function cacheHit(receipt, key, payload) {
  try {
    const saved = JSON.parse(fs.readFileSync(receipt, 'utf8'))
    return saved.key === key && saved.digest === treeDigest(payload)
  } catch {
    return false
  }
}

export function buildForkInputs(source, { rebuildPayload = false, env = process.env, versions, run }) {
  const app = path.join(source, 'apps/desktop')
  const payload = path.join(app, 'build/agent-payload')
  const receipt = path.join(app, 'build/fork-payload-cache.json')
  const key = payloadKey(source, env, versions)
  const hit = !rebuildPayload && cacheHit(receipt, key, payload)
  if (hit) console.log('[payload] cache hit; verified staged files')
  else {
    fs.rmSync(receipt, { force: true })
    console.log('[payload] staging from source')
    run('payload')
  }
  // Desktop rewrites the embedded install stamp and launcher manifest even on a renderer cache hit.
  run('build')
  if (payloadKey(source, env, versions) !== key) throw new Error('Payload inputs changed during the build; retry')
  const digest = treeDigest(payload)
  fs.writeFileSync(receipt + '.tmp', JSON.stringify({ key, digest }) + '\n')
  fs.renameSync(receipt + '.tmp', receipt)
}

if (isMain(import.meta.url)) {
  const { values } = parseArgs({ options: { 'rebuild-payload': { type: 'boolean' } } })
  const versions = [process.versions.node, execFileSync(process.env.HERMES_PYTHON, ['-VV'], { encoding: 'utf8' }).trim(),
    execFileSync('pnpm.exe', ['--version'], { encoding: 'utf8' }).trim()]
  const source = path.resolve(import.meta.dirname, '../../..')
  buildForkInputs(source, {
    rebuildPayload: values['rebuild-payload'], versions,
    run: script => execFileSync('pnpm.exe', ['run', script], { cwd: path.join(source, 'apps/desktop'), stdio: 'inherit' })
  })
}
