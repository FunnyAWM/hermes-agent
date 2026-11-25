import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

interface ChildLike {
  on: (event: 'error', listener: (error: Error) => void) => void
  unref: () => void
}

export interface GatewayQuitStopDeps {
  existsSync?: (path: string) => boolean
  spawn?: (command: string, args: string[], options: object) => ChildLike
}

/**
 * Stop the local gateway owner when Desktop really quits. Detached so quit never
 * waits on the gateway's drain; hiding to the tray never reaches this path.
 * Roots are tried in order (headless baked checkout first, then the update root).
 */
export function stopGatewayForQuit(
  roots: readonly (string | null | undefined)[],
  home: string,
  log: (message: string) => void,
  deps: GatewayQuitStopDeps = {}
): void {
  const exists = deps.existsSync ?? existsSync
  const run = deps.spawn ?? spawn
  const bin = process.platform === 'win32' ? path.join('Scripts', 'hermes.exe') : path.join('bin', 'hermes')

  const cliPath = roots
    .filter((root): root is string => Boolean(root))
    .flatMap(root => [path.join(root, 'venv', bin), path.join(root, '.venv', bin)])
    .find(exists)

  if (!cliPath) {
    log('[gateway] stop on quit skipped: no managed hermes CLI under the runtime roots')

    return
  }

  try {
    const child = run(cliPath, ['gateway', 'stop', '-y'], {
      env: { ...process.env, HERMES_HOME: home },
      detached: true,
      stdio: 'ignore',
      windowsHide: true
    })

    // A spawn that only fails asynchronously surfaces as an 'error' event;
    // without a listener it becomes an uncaught exception during quit.
    child.on('error', error => {
      log(`[gateway] stop on quit failed: ${error instanceof Error ? error.message : String(error)}`)
    })
    // Detach it: desktop quit must not wait on a CLI stop confirmation.
    child.unref()
    log(`[gateway] stop requested for desktop quit via ${cliPath}`)
  } catch (error) {
    log(`[gateway] stop on quit failed: ${error instanceof Error ? error.message : String(error)}`)
  }
}
