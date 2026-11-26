# Super Agent plugin

Super Agent is an opt-in Router that reuses the existing Gateway managed-worker process. It runs a
configurable number of tickets concurrently (two by default, up to eight). Every Assignment gets a dedicated Git worktree and a visible
`managed_worker` Session. Users can read those Sessions but cannot submit, control, or mutate them.
The Router can read user Sessions for context, but cannot submit, control, or mutate them.

Enable and configure it for each profile:

```yaml
plugins:
  enabled:
    - super_agent
  entries:
    super_agent:
      allow_gateway_injection: true
      settings:
        message_sources: ["*"]
        worker_provider: ""
        worker_model: ""
        max_concurrent_workers: "2"
        workspace_root: D:\VSCodeProjects\hermes-agent
        default_sandbox: workspace-write
        watchdog_seconds: 1800

terminal:
  sandbox:
    enabled: true
```

The Desktop exposes the same values in its first-level **Settings → Super Agent** page. `message_sources`
contains Gateway platform IDs; `["*"]` accepts every configured robot channel, including Weixin/OpenClaw,
and `[]` disables Gateway intake while leaving `/super-agent` available. `worker_provider` and
`worker_model` affect newly created Worker Sessions; blank values inherit the profile defaults.
`max_concurrent_workers` is the Router-wide limit. The current scheduler assigns one Worker to each Ticket.

`sandctl` must be installed or bundled. The Router accepts only `read-only`, `workspace-write`, and
`workspace-exec`; `full-access` is always rejected. Worker terminal calls are forced onto the local
sandbox backend even if the profile normally uses a remote terminal backend.

From any configured Gateway robot channel, including Weixin/OpenClaw, send:

```text
implement the requested change and run focused tests
```

Admitted text messages automatically open Router tickets. The optional `sa:` prefix still starts a
new ticket even on a subscribed thread. Commands and media stay with their channel handlers.
The sandbox token is optional and defaults to `default_sandbox`. The same operations are available
with `/super-agent new|status|list|tell|stop|retry|takeover|pause|resume|health`. Completion, failure,
takeover, and Watchdog messages return to the
originating robot conversation. Directed background delivery requires the explicit
`allow_gateway_injection: true` grant shown above.

`pause` persists across restarts. New tasks remain queued while launch and outbound delivery are
paused; already running Workers continue. `resume` restarts launch and delivery. `takeover` stops
the current admission and records an operator takeover while retaining the read-only Worker Session
and worktree. `stop` is an alias for `cancel`; retries are limited to three per Ticket.

The independent Watchdog records an alert after `watchdog_seconds` without a persisted Assignment
or Turn state change. It never retries, cancels, changes prompts, raises permissions, or changes the
sandbox. A long model or terminal operation can legitimately cross the threshold, so inspect the
Worker Session and worktree before using `takeover` or `stop`, then `retry` from the terminal state
if needed. `/super-agent health` shows
active alerts and recent pause, resume, recovery, retry, cancel, and takeover records; ticket
`status` includes its recovery record.

Set `message_sources: []` to return all channels to their own sessions. Disabling the plugin
also removes the command. Unload best-effort cancels every known active admission; durable
state is recovered into the queue with an audit record if the plugin is enabled again.

Worktrees are retained after success, failure, and cancellation so changes remain reviewable. Remove
them manually with `git worktree remove <path>` after review.
