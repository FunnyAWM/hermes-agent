"""Submit a single durable turn and reconcile its Gateway receipt."""

import asyncio
import logging
import time

from agent.i18n import t

logger = logging.getLogger(__name__)


def _remaining(turn):
    deadline = turn["deadline_at"]
    return None if deadline is None else max(0.0, deadline - time.time())


def _timeout_error(turn):
    return RuntimeError(t("super_agent.turn_timeout", seconds=turn["deadline_at"] - turn["started_at"]))


async def _submit(store, client, session_id, turn, text):
    remaining = _remaining(turn)
    if remaining == 0:
        await asyncio.to_thread(store.stop_turn, turn["id"], "timeout")
        raise _timeout_error(turn)
    try:
        async with asyncio.timeout(remaining):
            receipt = await client.rpc("prompt.submit", session_id=session_id, input_id=turn["input_id"],
                                       text=text, finite=True, unattended=True)
    except Exception:
        # The owner can commit a submit whose reply never reached this socket.
        await asyncio.to_thread(store.stop_turn, turn["id"], "submit_unconfirmed")
        if _remaining(turn) == 0:
            raise _timeout_error(turn) from None
        raise
    admission = receipt["admission_id"]
    await asyncio.to_thread(store.mark_turn_submitted, turn["id"], admission)
    return admission


async def _read_receipt(store, client, turn, args):
    remaining = _remaining(turn)
    try:
        if remaining == 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            return await client.rpc("prompt.receipt", **args, include_result=True)
    except Exception:
        if _remaining(turn) != 0:
            await asyncio.to_thread(store.stop_turn, turn["id"], "receipt_unconfirmed")
            raise
    await asyncio.to_thread(store.stop_turn, turn["id"], "timeout")
    try:
        stopped = await client.rpc("prompt.cancel", **args)
        if stopped.get("status") == "terminal":
            await asyncio.to_thread(store.confirm_stopped, turn["id"])
    except Exception:
        logger.exception("Worker timeout cancellation pending for %s", args["admission_id"])
    raise _timeout_error(turn)


def _completed(receipt, started_at):
    outcome = receipt.get("outcome")
    result = receipt.get("result")
    if outcome != "completed" or not isinstance(result, dict):
        raise RuntimeError(f"Worker turn ended with outcome {outcome or 'unknown'}")
    if result.get("failed") or result.get("interrupted"):
        raise RuntimeError(str(result.get("error") or f"Worker turn ended with outcome {outcome}"))
    usage = receipt.get("usage") or {}
    return str(result.get("final_response") or "Completed without a final response."), {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "cache_read_tokens": usage.get("cache_read_tokens", result.get("cache_read_tokens")),
        "duration_seconds": round(max(0.0, time.time() - started_at), 2),
    }


async def execute_turn(store, client, session_id, turn, text, poll_seconds):
    admission = turn["admission_id"]
    if turn["stop_reason"]:
        if turn["stop_reason"] == "timeout":
            raise _timeout_error(turn)
        raise RuntimeError(f"Worker stop pending: {turn['stop_reason']}")
    try:
        admission = admission or await _submit(store, client, session_id, turn, text)
        args = dict(session_id=session_id, admission_id=admission)
        while True:
            receipt = await _read_receipt(store, client, turn, args)
            if receipt.get("status") == "terminal":
                return _completed(receipt, turn["started_at"])
            if receipt.get("status") == "unknown":
                # The Gateway publishes `unknown` only after the managed child is gone (it kills
                # the child before settling the lost execution). Confirm the stop so a retry can
                # claim the next generation instead of waiting forever on `pending_stops`.
                await asyncio.to_thread(store.stop_turn, turn["id"], "receipt_unknown")
                await asyncio.to_thread(store.confirm_stopped, turn["id"])
                raise RuntimeError(f"Worker turn outcome is unknown for admission {admission}; inspect the Session before retrying")
            remaining = _remaining(turn)
            await asyncio.sleep(poll_seconds if remaining is None else min(remaining, poll_seconds))
    except asyncio.CancelledError:
        if admission:
            try:
                await client.rpc("prompt.cancel", session_id=session_id, admission_id=admission)
            except Exception:
                logger.exception("Super Agent unload cancel failed for admission %s", admission)
        raise
