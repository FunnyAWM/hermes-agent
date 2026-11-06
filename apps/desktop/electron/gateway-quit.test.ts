import assert from 'node:assert/strict'
import path from 'node:path'

import { test, vi } from 'vitest'

import { stopGatewayForQuit } from './gateway-quit'

const HERMES = process.platform === 'win32' ? path.join('Scripts', 'hermes.exe') : path.join('bin', 'hermes')

test('quit requests a detached gateway stop without waiting', () => {
  const unref = vi.fn()
  const spawn = vi.fn(() => ({ unref }))
  const log = vi.fn()
  const cli = path.join('C:\\source', '.venv', HERMES)

  stopGatewayForQuit(['C:\\missing', 'C:\\source'], 'C:\\home', log, {
    existsSync: candidate => candidate === cli,
    spawn: spawn as never
  })

  assert.equal(spawn.mock.calls[0][0], cli)
  assert.deepEqual(spawn.mock.calls[0][1], ['gateway', 'stop', '-y'])
  assert.equal((spawn.mock.calls[0][2] as { detached: boolean }).detached, true)
  assert.equal((spawn.mock.calls[0][2] as { env: NodeJS.ProcessEnv }).env.HERMES_HOME, 'C:\\home')
  assert.equal(unref.mock.calls.length, 1)
  assert.match(log.mock.calls[0][0], /stop requested/)
})

test('a missing CLI skips the stop and says why', () => {
  const spawn = vi.fn()
  const log = vi.fn()

  stopGatewayForQuit(['C:\\missing'], 'C:\\home', log, { existsSync: () => false, spawn: spawn as never })

  assert.equal(spawn.mock.calls.length, 0)
  assert.match(log.mock.calls[0][0], /no managed hermes CLI/)
})

test('a spawn failure is logged, never thrown', () => {
  const log = vi.fn()
  const cli = path.join('C:\\source', 'venv', HERMES)

  assert.doesNotThrow(() =>
    stopGatewayForQuit(['C:\\source'], 'C:\\home', log, {
      existsSync: candidate => candidate === cli,
      spawn: () => {
        throw new Error('spawn denied')
      }
    })
  )
  assert.match(log.mock.calls[0][0], /spawn denied/)
})
