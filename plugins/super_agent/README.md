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
        write_message_sources: ["same-as-read"]
        worker_provider: ""
        worker_model: ""
        max_concurrent_workers: "2"
        workspace_root: D:\VSCodeProjects\hermes-agent
        default_sandbox: workspace-write
        watchdog_seconds: 1800
        turn_timeout_seconds: 0
        history_retention_days: 0

terminal:
  sandbox:
    enabled: true
```

In **Settings → Super Agent**, the read and write dropdowns list the current profile's configured
messaging channels and always include the built-in bot. Previously selected channels remain visible
until you remove them. In YAML, use platform IDs such as
`telegram`, `weixin` or `local` (Desktop chats, including the phone-synced Bot Chat). Each list accepts `["*"]` for all
sources and `[]` for none. Older configurations inherit write permissions from `message_sources`,
equivalent to `write_message_sources: ["same-as-read"]`.

| Read (`message_sources`) | Write (`write_message_sources`) | Behavior |
| --- | --- | --- |
| No | No | Chat stays in its own session. |
| Yes | No | Can hand off tasks. Confirmation and clarification prompts are transient. Forwarded commands stay out of chat history, and the channel receives no task result. |
| No | Yes | Incoming messages stay in their own session. Each Worker turn reports to the configured Home Channel and topic. |
| Yes | Yes | The originating chat retains the user's input and each Worker report. The ticket confirmation appears as a transient system notice. |

For example, read `["local", "weixin"]` and write `["local", "telegram"]` to keep Bot Chat read/write,
use Weixin for read-only intake, and notify the Telegram Home Channel on completion. Configure the
Home Channel in the same profile. Write-only `local` uses that profile's canonical Bot Chat.
Delivery checks current permissions before every send. Reading `["*"]` includes Telegram:
writing `["telegram"]` in that configuration returns Telegram results to their originating
chats. To notify its Home Channel about other tickets, leave Telegram out of the read list.

`worker_provider` and
`worker_model` affect newly created Worker Sessions; blank values inherit the profile defaults.
`max_concurrent_workers` is the Router-wide limit. The current scheduler assigns one Worker to each Ticket.

`sandctl` must be installed or bundled. The Router accepts only `read-only`, `workspace-write`, and
`workspace-exec`; `full-access` is always rejected. Worker terminal calls are forced onto the local
sandbox backend even if the profile normally uses a remote terminal backend.

From any configured Gateway robot channel, including Weixin/OpenClaw, send:

```text
implement the requested change and run focused tests
```

Ordinary chat in Desktop, Web and admitted robot channels checks the sender's open tickets in the
current conversation. The `super_agent_route` tool reads a short brief for each candidate from the
original goal and latest user follow-up. A clear match appends the message. If multiple tickets
fit, the assistant asks which one. Questions stay in ordinary chat, and a clear execution request
creates a ticket immediately. A separate confirmation is used when intent is unclear. Declining
the handoff continues the original request in ordinary chat. Withdrawing an accepted task stops
the ticket and asks whether the ordinary assistant should continue it.
In robot channels, starting a message with a ticket ID directs the follow-up to that ticket.
An explicit `sa:` on a robot channel or `/super-agent new` creates a ticket immediately.
The ticket ID appears in a transient system notice outside model history. IM platforms that support
deletion use the configured ephemeral lifetime. If there is no writable destination, the same
notice points to settings and each turn's result remains available in ticket status.
Subscribed threads continue to deliver updates to their subscribers. Commands and media keep their channel handlers.
The sandbox token is optional and defaults to `default_sandbox`. The same operations are available
with `/super-agent new|status|list|tell|stop|retry|takeover|pause|resume|health`. Completion, failure,
takeover, and Watchdog messages return to the
originating robot conversation. Directed background delivery requires the explicit
`allow_gateway_injection: true` grant shown above.

Each completed or failed Worker turn produces one report per writable destination. The last report
also serves as the completion notice. The dispatcher retries temporary failures with backoff.
Hermes stores one copy in the destination conversation per report. An external chat may see a
duplicate if the platform accepts the message and the acknowledgment is lost.
`/super-agent status <id>` shows turn results, token usage, subscriptions, handbacks and delivery state.
`/super-agent redeliver <id> [delivery-id]` requeues a failed or uncertain notice without running
the task again. Pre-upgrade pending notices resume automatically. Check the destination chat before
manually resending a pre-upgrade failed or uncertain notice. Those records lack a stable delivery ID.
`/super-agent observe-log <thread-key>` shows recent observations for the calling thread.

`pause` persists across restarts. New tasks remain queued while launch and outbound delivery are
paused; already running Workers continue. `resume` restarts launch and delivery. `takeover` stops
the current admission and records an operator takeover while retaining the read-only Worker Session
and worktree. `stop` is an alias for `cancel`; retries are limited to three per Ticket.

The independent Watchdog records an alert after `watchdog_seconds` without a persisted state change
or Worker tool activity. To bound execution, set `turn_timeout_seconds` in Super Agent settings.
The limit is saved when a turn starts and survives restart. Zero disables it. A long model or
terminal operation can legitimately cross the Watchdog threshold, so inspect the
Worker Session and worktree before using `takeover` or `stop`, then `retry` from the terminal state
if needed. `/super-agent health` shows
active alerts and recent pause, resume, recovery, retry, cancel, and takeover records; ticket
`status` includes its recovery record.

Set `message_sources: []` to disable task handoff from every source, including chat commands. Disabling the plugin
also removes the command. Unload best-effort cancels every known active admission; durable
state is recovered into the queue with an audit record if the plugin is enabled again.

Name a child Git repository in the task (for example, `检查 kourichat-next 的构建`), or give its
path relative to `workspace_root` for a deeper project. The ticket keeps the selected repository
for follow-ups and retries. Without a project reference it uses the configured root.
New assignments start from the selected repository's committed `HEAD`, in
`<repository>/.super-agent-worktrees/<ticket>-g<generation>`. Local edits in independent nested
repositories and submodule checkouts do not block their parent. Changes belonging to the selected
repository, including staged submodule pointers and ordinary untracked files, still need to be committed or stashed.

Worktrees are retained after success, failure, and cancellation so changes remain reviewable. Remove
them manually with `git worktree remove <path>` after review.

`history_retention_days` defaults to zero, keeping history indefinitely. A positive whole number
expires old terminal tickets and their reports in batches after delivery and stop reconciliation.
The profile retains event identities to prevent replay from creating new work. Cleanup leaves the
Git worktree and operator edits in place.
