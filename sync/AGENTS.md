# sync/ — KouriChat Link PC-side device-chain sync

The PC docking point for the Android client's (kourichat-next) "KouriChat Link" LAN
device-chain: the master runs a TLS sync server on port 27890, secondaries pull a
whitelisted Markdown set, and the 24-word BIP39 mnemonic derives every key. The Android
sources are the protocol's fact source — this package is a 1:1 Python port, and the two
ends only ever change together.

## Non-negotiables

- **Wire compatibility is the contract.** Framing (4-byte big-endian length + UTF-8
  JSON, 32 MiB cap), the handshake (`hello → challenge → auth → auth_ok`, HMAC proof
  over `"kourichat-sync-v1|<nc>|<ns>"`), the two-way negotiation (v2.8: `list` exchanges
  full manifests, then `push`/`push_ok` carries the locally-newer records; newest-HLC
  wins, both ends archive overwrites), the envelope (AES-256-GCM, AAD =
  `recordId|hlc-canonical|deviceId`, HKDF salt/info pairs) and the JSON field names must
  match `kourichat-next` `app/src/main/java/org/funnyawm/kourichatnext/data/sync/`
  exactly. `tests/sync/test_golden_vectors.py` pins the crypto primitives against RFC
  5869 / BIP39 / the Kotlin suite's own vectors.
- **kotlinx JSON parity.** The Android side decodes with plain `Json`
  (`encodeDefaults=false, ignoreUnknownKeys=false`): Python omits default fields on
  encode, accepts absent defaults on decode, and rejects unknown keys / duplicate keys —
  both ends fail closed together.
- **Fail closed.** An envelope that fails AAD, digest or JSON rejects the WHOLE batch
  (HM-6003); a corrupt `.meta/state.json` is never treated as "unpaired" (HM-6004); a
  fingerprint mismatch kills the connection before any record moves (TOFU).
- **Whitelist parity (v3.0).** Only `SOUL.md`, `memories/MEMORY.md`, `memories/USER.md`,
  `notes/**.md`, `skills/**.md` and chat sessions as
  `sessions/<id>/{meta.json,messages.jsonl}` sync (the `sessions/current` pointer and
  dot files never do); `AGENTS.md`, `.versions/`, `.meta/` never do. The sole rule
  source is Android's `MemoryRepository.isSyncablePath` — change `sync/scope.py` only
  together with it.
- **Bidirectional, still master-authored.** v2.8 lets the secondary push locally-newer
  records and the master apply them with the same archive-then-overwrite + HLC merge —
  but there is still no interactive conflict arbitration and no CRDT; newest HLC simply
  wins, and the overwritten copy survives in `.versions/`. No key rotation
  (`generation` is 1). Local-only files are never touched.
- **Truststore containment.** hermes injects truststore globally at boot; on CPython 3.14
  that corrupts `check_hostname`/`verify_mode` assignment and breaks server-side sockets.
  Build every sync TLS context through `sync/identity.py`'s `master_tls_context` /
  `tofu_client_ssl_context` (they lift the injection for the one context) — never raw
  `ssl.SSLContext`.

## Chat-transcript bridge (PC ↔ chain)

`hermes_cli/sync_sessions_bridge.py` is the seam between the Bot's own history
(state.db) and the synced session files: `export_bot_sessions` writes the main-loop
surfaces' conversations as KouriChat session files (uuid directory names; every kotlinx
default serialized; incremental via `.meta/session-bridge.json`), and
`import_synced_sessions` folds every landed file into the Bot's SINGLE history — the
canonical `Bot Chat` session (created on first sync when the Bot has none) — never into
one session row per phone conversation. Imported and local messages merge by
role+content+createdAt, the Android ``mergeMessages`` identity, so nothing ever
duplicates. Once a `Bot Chat` exists, export carries only that session: the Bot has one
history, and the session list is not a sync target. The manager takes both as
`pre_sync`/`post_sync` hooks (the sync package stays storage-agnostic).

## Discovery, QR and exit sync

- `sync/discovery.py` mirrors the Android NSD records (`_kourichat-sync._tcp.` / service
  name `Kouri Harness`) via the OPTIONAL `zeroconf` dependency: the master registers
  with `start_server` (auto-started by `create_chain`, and by a master-role `sync_now`
  when it is not serving), and a secondary rediscovers on EVERY cycle — the saved
  `master_endpoint` is a fallback, not the truth, because DHCP/Wi-Fi-roam/VPN moves the
  address. Browsing is capped at 2.5s and followed by a direct TCP probe of the local
  /24 on the sync port (`probe_subnet`), which is what makes discovery work on networks
  where multicast is filtered; a false positive costs one failed handshake, never a
  wrong pair. Without the dependency both mDNS paths are safe no-ops (the probe and the
  manual endpoint still work); do not make zeroconf a hard dependency without a lockfile
  in hand. Declaring
  the `[sync]` extra means editing pyproject.toml AND relocking (`hermes pm lock`, which
  needs github.com reachable for the psutil git pin) in the same commit — never declare
  it without the lock, or `uv sync --locked` breaks for everyone.
- **Address-free pairing** (`SyncManager.join_chain(words)` with no endpoint, Android
  `joinChain`): browse for `_JOIN_DISCOVERY_TIMEOUT_MS` (8 s, the Android
  `JOIN_DISCOVERY_TIMEOUT_MS`), then handshake every candidate in turn (`_join_at`);
  discovery records never decide chain membership — only the TLS + challenge proof does,
  so a peer on ANOTHER chain just fails and the loop moves on. All candidates failing
  leaves nothing paired and records `lastSyncError`. The join negotiation is the v2.8
  two-way exchange: `engine.compute_outgoing` pushes what the joining device already has
  up to the master, so pairing is not pull-only.
- `sync/qr.py` parses the pairing payload the phone shows (`kourichat-sync:v1|words|host|port`);
  `hermes sync pair --qr "<text>"` accepts scanner output.
- `SyncManager.sync_on_exit` is the user-facing exit contract: a secondary runs one last
  negotiation (saved endpoint → discovery), a master folds local edits into the record
  log. Wired via `hermes sync watch`/`serve` exits and the desktop backend's atexit
  (`web_server._kourichat_link_exit_sync`). An exit path must never raise.

## Layout

- Protocol: `framing.py`, `protocol.py`, `crypto.py`, `keys.py`, `mnemonic.py`,
  `records.py`, `clock.py`, `bip39_english_words.txt` (data file, checksum-verified).
- State: `state_store.py` (`.meta/state.json`), `scope.py` (whitelist), `identity.py`
  (self-signed EC P-256 at `.meta/identity/`).
- Network: `client.py` (TOFU + negotiation), `server.py` (thread-per-connection master
  service; `SO_EXCLUSIVEADDRUSE` on Windows so a port conflict fails loudly),
  `discovery.py` (optional zeroconf), `qr.py` (pairing payload).
- Orchestration: `engine.py` (record log + apply + `compute_outgoing` + `push`),
  `manager.py` (lifecycle, negotiation wiring, HM-600x mapping, `sync_on_exit`),
  `errors.py`, `scheduler.py` (periodic loop for `hermes sync watch` and long-lived hosts).

## Kouri Harness seams (outside this package)

- CLI: `hermes_cli/sync_cmd.py` (`hermes sync ...`) — settings come from the
  `kourichat_link` section of config.yaml (`sync_cmd.update_settings` is the write path
  behind the desktop settings UI), the mnemonic only ever lives in `.env`
  (`KOURICHAT_SYNC_MNEMONIC`), and the shipped defaults live in
  `hermes_cli/config_defaults_kourichat.py` (config_defaults.py is ratchet-locked).
- Desktop: `hermes_cli/web_routers/kourichat_link.py` (REST) and
  `tui_gateway/methods_sync.py` + `tui_gateway/contracts/sync.py` (JSON-RPC) — both run
  the per-home shared manager (`sync_cmd.shared_manager()`) under the REQUEST's profile
  scope; a multiplexing backend must never answer for the launch profile by default
  (HX002/HX004).
- Long-lived hosts opt into `sync.scheduler.SyncScheduler`; a scheduler thread binds its
  owning profile's scope explicitly (`contextvars.copy_context().run` — the stores
  resolve config per current scope).
