"""KouriChat Link PC-side sync: the desktop docking point for the Android device chain.

The Android client (kourichat-next, M4) runs a TLS sync server on the master device
(port 27890) and pulls on secondaries; auth and end-to-end encryption derive entirely from
a shared 24-word BIP39 mnemonic. This package is a 1:1 Python port of the Android
``data/sync/`` modules — file names and semantics mirror the Kotlin sources so the two
ends can be reviewed side by side, and the protocol must only ever change together with
its Kotlin counterpart.

Phase-1 semantics: the master is the sole writer, secondaries pull whole-file Markdown
within the whitelist (:mod:`sync.scope`) with hybrid-logical-clock ordering and
``.versions/`` archival before overwrite or delete. There is no server-side write
endpoint, no CRDT, no key rotation (``generation`` stays 1) — see the Android PRD §7.7.
"""
