# KouriChat Link — device-chain sync

KouriChat Link mirrors your files between the Android client (Kouri Harness) and this
desktop app over your local network. There is no cloud server: one device is the
**chain master** (it serves the data), the others join as **secondaries**. Both roles
are available on PC.

Sync is **two-way**: every negotiation exchanges full manifests and each side pushes
whatever is locally newer — newest-version-wins by a hybrid logical clock, and every
overwritten or deleted copy is archived to `.versions/` first, so nothing is lost
silently.

Only a fixed whitelist syncs — `SOUL.md`, `memories/MEMORY.md`, `memories/USER.md`,
everything Markdown under `notes/` and `skills/`, and **chat sessions** as
`sessions/<id>/meta.json` + `messages.jsonl` (v3.0). Sessions, databases, settings and
keys never leave the device otherwise.

## Chat records flow into the Bot's history

The device chain carries chat sessions both ways:

- Conversations you had **with the Bot on this PC** are exported from the Bot's single
  history and pushed to the chain — the phone sees them as their own.
- Chats that happened **on the phone** land in this PC's `sessions/` mirror and are
  folded into the Bot's **one history** — the canonical `Bot Chat` session — instead of
  appearing as a new conversation in the sidebar. The Bot has a single memory of its
  conversations, so a phone chat reads as part of it. If the Bot has no history yet, the
  first sync creates it.
- Other desktop sessions (scratch chats, subagent runs) stay on this PC; once a
  `Bot Chat` exists it is the only session the chain carries.

Merging is duplicate-safe: a message is identified by role + content + timestamp, so a
repeated sync never doubles a row. Overlapping phone sessions are interleaved by their
own timestamps into that single history.

## The mnemonic is everything

Pairing rests on a 24-word BIP39 mnemonic — the **only** credential for the whole chain.
Both the Android app and the desktop derive every key from it; nothing secret ever
travels on the wire (authentication is a challenge-response proof, records are
end-to-end encrypted).

- Write it on **paper** and keep it safe. It cannot be recovered if lost.
- Anyone holding the words can join the chain.
- On PC the words live in `.env` under `KOURICHAT_SYNC_MNEMONIC` — the secrets file, never
  `config.yaml`.

## Pair the PC as a secondary (mirror the phone)

1. On the Android app: create the chain and start sync.
2. On the PC, pick whichever fits — **no address needed** when both devices are on the
   same LAN:

   ```bash
   hermes sync pair                               # scan the LAN, then ask for the words
   hermes sync pair 192.168.1.20:27890            # manual address (different subnet, VPN…)
   hermes sync pair --qr "kourichat-sync:v1|…"    # paste what a QR scanner read
   ```

   (enter the 24 words when asked, or pass `--words "…"`).

   Without an address the PC browses the LAN for up to 8 seconds and **tries every
   candidate it finds in turn** — a master hosting a *different* chain fails the
   handshake and the scan moves on, so only the master holding these words can be
   adopted. A browse that finds nothing is followed by a direct TCP probe of the local
   /24 on the sync port, so a firewall or router that drops multicast does not hide the
   master. If nothing is found, nothing is paired and the command says so; fall back to
   the manual address form.
3. Check with `hermes sync status`. **Every** sync rediscovers the master, so the saved
   address is only a hint: when the phone changes networks (DHCP renewal, Wi-Fi roam,
   VPN), the next sync finds the new address and writes it back.

Whatever the PC already has in the whitelist is **pushed up during pairing** (not just
pulled down): notes written before joining reach the master in the same handshake.

## Run the PC as the master (the phone pulls)

```bash
hermes sync create-chain   # prints the 24 words once — write them down
hermes sync serve          # listens on all interfaces, port 27890
```

Then join from the Android app with those words. Creating the chain starts the server
right away, and the PC master advertises itself on the LAN (mDNS, when the optional
`zeroconf` package is installed), so the phone can find it without typing an address.

## Everyday commands

| Command | What it does |
| --- | --- |
| `hermes sync status` | Role, pairing, counters, last sync and error |
| `hermes sync now` | One two-way negotiation (pull what's newer, push what's newer) |
| `hermes sync watch` | Sync every `kourichat_link.auto_sync_minutes` (0 = off) |
| `hermes sync disconnect` | Leave the chain as secondary; synced files stay |
| `hermes sync reset-chain` | Master kills the chain; prompts before acting |

The desktop's **Capabilities → Link** tab drives everything with a UI: pairing (paste the
24 words — the master is discovered on the LAN, no address to type), sync status and
counters, start/stop serving, and the settings — periodic-sync interval and, for a
master, the listen port.

## Exit sync

When the PC software leaves, it tries to find the chain and sync one last time: a
secondary negotiates with the saved master (falling back to a LAN scan), a master folds
its local edits into the record log. `hermes sync watch`/`serve` do this on Ctrl-C, and
the desktop backend does it on shutdown. Failures never block the exit.

## Configuration

```yaml
kourichat_link:
  role: disabled        # disabled | master | secondary (managed by `hermes sync`)
  master_endpoint: ""   # secondary: the master's host:port
  listen_port: 27890    # master: the TLS sync port
  auto_sync_minutes: 0  # 0 = manual only (15/30/60/360/1440 map to the phone's choices)
```

## Notes and limits

- Conflicts are not interactively arbitrated: the newest hybrid-logical-clock version
  wins per file, the loser survives in `.versions/`. There is no line-level merge.
- A file only newer on one side simply wins; local-only files a master never recorded
  are never touched.
- Windows shows a firewall prompt the first time the master serves on port 27890; allow
  it for your private network.
- mDNS discovery (address-free pairing) needs the optional `zeroconf` package
  (`uv pip install zeroconf` in the runtime's environment — not a core dependency, so a
  lean install keeps pairing by address). Without it, or on a network that blocks
  multicast, the /24 TCP probe still finds a master on the same subnet; only a master on
  a different subnet needs the address typed in.
- Both ends must run the same protocol generation; a mismatch fails closed with an
  `HM-600x` error rather than corrupting data.
