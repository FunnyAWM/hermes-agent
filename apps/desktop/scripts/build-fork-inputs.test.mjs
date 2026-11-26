import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, it } from 'vitest'
import { buildForkInputs } from './build-fork-inputs.mjs'

it('reuses a stamped payload, rebuilding on changed inputs, damaged bytes or a failed stage', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fork-build-'))
  try {
    const git = (...args) => execFileSync('git', args, { cwd: root })
    git('init', '--quiet')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '--allow-empty', '-qm', 'fixture')
    const payload = path.join(root, 'apps/desktop/build/agent-payload')
    const file = path.join(payload, 'runtime')
    let builds = 0
    const stages = []
    const options = { env: {}, versions: ['test'], run(stage) {
      stages.push(stage)
      if (stage === 'payload') {
        fs.mkdirSync(payload, { recursive: true })
        fs.writeFileSync(file, 'original')
      } else fs.writeFileSync(path.join(payload, 'install-stamp.json'), String(++builds))
    } }
    function build(extra) {
      stages.length = 0
      buildForkInputs(root, { ...options, ...extra })
      return [...stages]
    }
    expect(build()).toEqual(['payload', 'build'])
    expect(build()).toEqual(['build'])
    expect(build()).toEqual(['build'])
    fs.writeFileSync(file, 'modified')
    expect(build()).toEqual(['payload', 'build'])
    fs.mkdirSync(path.join(root, 'scripts'))
    fs.writeFileSync(path.join(root, 'scripts', 'builder.py'), 'changed')
    expect(build()).toEqual(['payload', 'build'])
    options.env.UV_DEFAULT_INDEX = 'http://mirror.example/simple/'
    expect(build()).toEqual(['payload', 'build'])
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '--allow-empty', '-qm', 'next')
    expect(build()).toEqual(['payload', 'build'])
    expect(() => build({ rebuildPayload: true, run() { throw new Error('interrupted') } })).toThrow('interrupted')
    expect(build()).toEqual(['payload', 'build'])
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
