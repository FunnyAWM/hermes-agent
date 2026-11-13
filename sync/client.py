"""Secondary-side client with TOFU fingerprint pinning.

Mirrors the Android ``SyncClient.kt`` (v2.8 two-way negotiation): hello → challenge →
auth → list → negotiate → bye over TLS. The manifest exchange feeds an ``exchange``
callback that applies the remote batch and returns locally-newer records; non-empty
outgoing is pushed and acknowledged with ``push_ok``. The server certificate is a
self-signed identity whose SHA-256 fingerprint *is* the identity: the first connection
records it (trust-on-first-use, Android VALIDATION §3), any later mismatch fails closed
(HM-6003). The fingerprint check runs right after the TLS handshake, before any protocol
byte — no secret leaves this side before the pin matches.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from typing import IO, Callable

from sync.errors import SyncErrorCode, SyncError
from sync.identity import fingerprint_of_der, tofu_client_ssl_context
from sync.keys import SyncKeys
from sync.protocol import (
    TYPE_AUTH,
    TYPE_AUTH_FAIL,
    TYPE_AUTH_OK,
    TYPE_BYE,
    TYPE_CHALLENGE,
    TYPE_ERROR,
    TYPE_HELLO,
    TYPE_LIST,
    TYPE_LIST_RESULT,
    TYPE_PUSH,
    TYPE_PUSH_OK,
    SyncEnvelope,
    SyncMessage,
    new_nonce,
    proof,
    read_message,
    write_message,
)

_HANDSHAKE_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class SyncExchangeResult:
    #: SHA-256 hex of the server certificate observed on this connection.
    fingerprint: str


class SyncClient:
    def sync(
        self,
        host: str,
        port: int,
        device_id: str,
        keys: SyncKeys,
        pinned_fingerprint: str | None,
        exchange: Callable[[list[SyncEnvelope]], list[SyncEnvelope]],
    ) -> SyncExchangeResult:
        """One full two-way negotiation (Android v2.8): manifest exchange → the ``exchange``
        callback applies the remote batch and returns locally-newer records to push →
        ``push``/``push_ok`` → bye. Network failures raise HM-6001, auth failure HM-6005
        and protocol shape failures HM-6003."""
        try:
            raw = socket.create_connection((host, port), timeout=_HANDSHAKE_TIMEOUT_S)
        except OSError as error:
            raise SyncError(SyncErrorCode.UNREACHABLE, f"{host}:{port}") from error
        stream: ssl.SSLSocket | None = None
        try:
            stream = _tofu_socket(raw, pinned_fingerprint)
            result = self._sync_over(stream, device_id, keys, exchange)
        except SyncError:
            raise
        except (OSError, EOFError) as error:
            raise SyncError(SyncErrorCode.UNREACHABLE, str(error)) from error
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    # Windows close-race (10053) when both ends shut down after BYE: the
                    # sync already completed, so a close-time abort must not mask it
                    # (Kotlin ``use{}`` suppresses close() failures the same way).
                    pass
        return result

    def _sync_over(
        self,
        stream: IO[bytes],
        device_id: str,
        keys: SyncKeys,
        exchange: Callable[[list[SyncEnvelope]], list[SyncEnvelope]],
    ) -> SyncExchangeResult:
        fingerprint = _observed_fingerprint(stream)
        nonce_client = new_nonce()
        write_message(stream, SyncMessage(TYPE_HELLO, device_id=device_id, nonce_client=nonce_client))
        challenge = read_message(stream)
        if challenge is None or challenge.type != TYPE_CHALLENGE or not challenge.nonce_server:
            raise SyncError(SyncErrorCode.VERIFY, "closed before challenge")
        write_message(
            stream,
            SyncMessage(TYPE_AUTH, proof=proof(keys.auth_key(), nonce_client, challenge.nonce_server)),
        )
        verdict = read_message(stream)
        if verdict is None:
            raise SyncError(SyncErrorCode.VERIFY, "closed before auth verdict")
        if verdict.type == TYPE_AUTH_FAIL:
            raise SyncError(SyncErrorCode.AUTH, "challenge-response rejected")
        if verdict.type != TYPE_AUTH_OK:
            raise SyncError(SyncErrorCode.VERIFY, "unexpected auth verdict")
        write_message(stream, SyncMessage(TYPE_LIST))
        listing = read_message(stream)
        if listing is None or listing.type != TYPE_LIST_RESULT:
            raise SyncError(SyncErrorCode.VERIFY, "closed before list result")
        outgoing = exchange(listing.envelopes)
        if outgoing:
            write_message(stream, SyncMessage(TYPE_PUSH, envelopes=outgoing))
            verdict2 = read_message(stream)
            if verdict2 is None:
                raise SyncError(SyncErrorCode.VERIFY, "closed before push verdict")
            if verdict2.type == TYPE_ERROR:
                raise SyncError(SyncErrorCode.VERIFY, verdict2.reason or "push rejected")
            if verdict2.type != TYPE_PUSH_OK:
                raise SyncError(SyncErrorCode.VERIFY, "unexpected push verdict")
        write_message(stream, SyncMessage(TYPE_BYE))
        return SyncExchangeResult(fingerprint)


def _tofu_socket(raw: socket.socket, pinned_fingerprint: str | None) -> ssl.SSLSocket:
    stream = tofu_client_ssl_context().wrap_socket(raw)
    der = stream.getpeercert(binary_form=True)
    if not der:
        stream.close()
        raise SyncError(SyncErrorCode.VERIFY, "server presented no certificate")
    if pinned_fingerprint is not None and fingerprint_of_der(der) != pinned_fingerprint.lower():
        stream.close()
        raise SyncError(SyncErrorCode.VERIFY, "server fingerprint mismatch")
    return stream


def _observed_fingerprint(stream: ssl.SSLSocket) -> str:
    der = stream.getpeercert(binary_form=True)
    if not der:
        raise SyncError(SyncErrorCode.VERIFY, "server certificate vanished mid-session")
    return fingerprint_of_der(der)
