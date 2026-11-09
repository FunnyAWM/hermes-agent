"""Master-side sync service: TLS listener + challenge-response auth + list/fetch.

Mirrors the Android ``SyncServer.kt``: the TLS identity is injected (the self-signed
:mod:`sync.identity`), no record moves before authentication, each connection gets its
own thread, and the service starts and stops with the role switch — no resident daemon
(the Android 同 12 placeholder keeps the same shape). The listener binds all interfaces:
any LAN address of this device accepts connections (VALIDATION §2).
"""

from __future__ import annotations

import contextvars
import socket
import ssl
import sys
import threading
from typing import Protocol

from sync.errors import SyncErrorCode, SyncError
from sync.identity import FileSyncIdentity, master_tls_context
from sync.protocol import (
    TYPE_AUTH,
    TYPE_AUTH_FAIL,
    TYPE_AUTH_OK,
    TYPE_BYE,
    TYPE_CHALLENGE,
    TYPE_ERROR,
    TYPE_FETCH,
    TYPE_HELLO,
    TYPE_LIST,
    TYPE_LIST_RESULT,
    TYPE_PUSH,
    TYPE_PUSH_OK,
    TYPE_RECORD,
    SyncEnvelope,
    SyncMessage,
    new_nonce,
    proof,
    read_message,
    write_message,
)

_BACKLOG = 4
_HANDSHAKE_TIMEOUT_S = 15.0
_ACCEPT_POLL_S = 0.5


class SyncRecordSource(Protocol):
    """Master-side record source (implemented by :class:`sync.engine.SyncEngine`);
    only called after authentication succeeds."""

    def auth_key(self) -> bytes | None: ...

    def list_records(self) -> list[SyncEnvelope]: ...

    def record(self, record_id: str) -> SyncEnvelope | None: ...

    def push(self, envelopes: list[SyncEnvelope]) -> SyncErrorCode | None:
        """Records a secondary pushed as locally-newer; applied with the same
        archive-then-overwrite and HLC merge as the secondary's own apply."""
        ...


class SyncServer:
    def __init__(self, port: int, identity: FileSyncIdentity, records: SyncRecordSource) -> None:
        self._port = port
        self._identity = identity
        self._records = records
        self._listener: socket.socket | None = None
        self._running = threading.Event()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def bound_port(self) -> int | None:
        return self._listener.getsockname()[1] if self._listener else None

    def start(self) -> None:
        """Bind and serve; port-in-use or TLS failures raise HM-6006, nothing is swallowed."""
        if self._running.is_set():
            return
        context = master_tls_context(self._identity)
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == "win32":
                # Windows SO_REUSEADDR permits a silent second bind and the two sockets
                # then fight over connections; exclusive binds fail loudly instead.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("", self._port))
            listener.listen(_BACKLOG)
        except OSError as error:
            raise SyncError(SyncErrorCode.SERVER_START, str(error)) from error
        self._listener = listener
        self._running.set()
        # Run the loop under the caller's snapshot: the record source reads config-scoped
        # paths, so a multiplexing host's accept loop must keep the owning profile's scope.
        scope = contextvars.copy_context()
        threading.Thread(target=scope.run, args=(self._accept_loop, listener, context), daemon=True, name="kourichat-link-server").start()

    def stop(self) -> None:
        self._running.clear()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

    def _accept_loop(self, listener: socket.socket, context: ssl.SSLContext) -> None:
        listener.settimeout(_ACCEPT_POLL_S)
        while self._running.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            # Connection threads inherit the accept loop's snapshot via copy_context().
            conn_scope = contextvars.copy_context()
            threading.Thread(
                target=conn_scope.run,
                args=(self._handle, context, client),
                daemon=True,
                name="kourichat-link-conn",
            ).start()

    def _handle(self, context: ssl.SSLContext, raw: socket.socket) -> None:
        try:
            raw.settimeout(_HANDSHAKE_TIMEOUT_S)
            with context.wrap_socket(raw, server_side=True) as stream:
                if self._handshake(stream):
                    self._serve_requests(stream)
        except (OSError, ssl.SSLError):
            # TLS handshake failures and abrupt disconnects are routine (scans, dropouts).
            pass

    def _handshake(self, stream: ssl.SSLSocket) -> bool:
        hello = read_message(stream)
        if hello is None or hello.type != TYPE_HELLO or not hello.nonce_client:
            return False
        server_nonce = new_nonce()
        write_message(stream, SyncMessage(TYPE_CHALLENGE, nonce_server=server_nonce))
        auth = read_message(stream)
        if auth is None or auth.type != TYPE_AUTH or not auth.proof:
            return False
        auth_key = self._records.auth_key()
        expected = proof(auth_key, hello.nonce_client, server_nonce) if auth_key else None
        if expected is None or expected != auth.proof:
            write_message(stream, SyncMessage(TYPE_AUTH_FAIL))
            return False
        write_message(stream, SyncMessage(TYPE_AUTH_OK))
        return True

    def _serve_requests(self, stream: ssl.SSLSocket) -> None:
        while True:
            message = read_message(stream)
            if message is None:
                return
            if message.type == TYPE_LIST:
                write_message(stream, SyncMessage(TYPE_LIST_RESULT, envelopes=self._records.list_records()))
            elif message.type == TYPE_FETCH:
                envelope = self._records.record(message.record_id) if message.record_id else None
                if envelope is None:
                    write_message(stream, SyncMessage(TYPE_ERROR, reason="record not found"))
                else:
                    write_message(stream, SyncMessage(TYPE_RECORD, record_id=envelope.record_id, envelopes=[envelope]))
            elif message.type == TYPE_PUSH:
                error = self._records.push(message.envelopes)
                if error is None:
                    write_message(stream, SyncMessage(TYPE_PUSH_OK))
                else:
                    write_message(stream, SyncMessage(TYPE_ERROR, reason=error.wire_name))
            elif message.type == TYPE_BYE:
                return
            else:
                write_message(stream, SyncMessage(TYPE_ERROR, reason="unsupported request"))
                return
