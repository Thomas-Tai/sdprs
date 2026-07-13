"""
Non-blocking event capture helpers for the edge glass node.

This module decouples post-trigger video capture from the main detection
loop so that neither the post-roll wait nor the MP4 encode ever blocks
frame ingestion:

- ``PendingEventTracker`` records a trigger and releases it only once its
  post-roll window has elapsed, so the main loop can keep buffering frames
  and simply poll ``due(now)`` each iteration (no ``sleep``, no second
  blocking camera read).
- ``slice_window`` extracts the bare ndarrays for a ``[t_start, t_end]``
  window out of a frozen ``(timestamp, frame)`` circular-buffer snapshot.
- ``clamp_capture_window`` enforces the buffer invariant
  ``pre_roll + post_roll + margin <= duration_seconds`` so a requested
  window can never exceed what the circular buffer actually retains.
- ``EncodeWorker`` runs ``encode_mp4`` (and the follow-up upload-queue
  enqueue) on a dedicated daemon thread behind a bounded, drop-newest
  queue, keeping encode latency off the capture path and memory bounded.

The main detection loop wires these together; this module has no
dependency on the camera, detectors, or the main loop itself.
"""

import logging
import queue
import threading
from datetime import datetime
from typing import Callable, List, Tuple

import numpy as np

logger = logging.getLogger("event_capture")

__all__ = [
    "PendingEvent",
    "PendingEventTracker",
    "slice_window",
    "clamp_capture_window",
    "EncodeWorker",
]


class PendingEvent:
    """A triggered event awaiting its post-roll window to elapse."""

    def __init__(self, trigger_ts: float, metadata: dict):
        self.trigger_ts = trigger_ts
        self.metadata = metadata


class PendingEventTracker:
    """Non-blocking tracker: registers triggers, releases them once the
    post-roll window has elapsed.

    The main loop polls :meth:`due` every iteration; events are held in
    registration order and released (and removed) in that same order once
    ``trigger_ts + post_roll <= now``.
    """

    def __init__(self, post_roll_seconds: float):
        self._post_roll = float(post_roll_seconds)
        self._pending: List[PendingEvent] = []

    def add(self, trigger_ts: float, metadata: dict) -> None:
        """Register a new trigger to be released after the post-roll window."""
        self._pending.append(PendingEvent(trigger_ts, metadata))

    def due(self, now: float) -> List[PendingEvent]:
        """Return + REMOVE events whose ``trigger_ts + post_roll <= now``.

        Registration order is preserved in the returned list and in the
        remaining pending list.
        """
        ready = [e for e in self._pending if e.trigger_ts + self._post_roll <= now]
        if ready:
            ready_ids = {id(e) for e in ready}
            self._pending = [e for e in self._pending if id(e) not in ready_ids]
        return ready

    def __len__(self) -> int:
        return len(self._pending)


def slice_window(
    frames: List[Tuple[float, "np.ndarray"]], t_start: float, t_end: float
) -> List["np.ndarray"]:
    """Return the BARE ndarrays (timestamps dropped) whose timestamp falls
    within ``[t_start, t_end]`` inclusive, in their original order.
    """
    return [f for (ts, f) in frames if t_start <= ts <= t_end]


def clamp_capture_window(
    duration_seconds: float,
    pre_roll: float,
    post_roll: float,
    margin: float = 1.0,
) -> Tuple[float, float]:
    """Enforce the buffer invariant ``pre_roll + post_roll + margin <=
    duration_seconds``.

    If the requested window fits, return ``(pre_roll, post_roll)``
    unchanged. Otherwise shrink ``pre_roll`` first (floor 0), then
    ``post_roll`` (floor 0), log a WARNING, and return the adjusted values.
    The returned pair always satisfies ``pre + post + margin <= duration``.
    """
    pre, post = float(pre_roll), float(post_roll)
    budget = float(duration_seconds) - float(margin)
    if pre + post <= budget:
        return pre, post
    # Shrink pre_roll first (floor 0).
    new_pre = max(0.0, budget - post)
    if new_pre + post <= budget:
        logger.warning(
            "capture window too large for buffer: pre=%.1f post=%.1f margin=%.1f "
            "> duration=%.1f; clamped pre_roll -> %.1f",
            pre,
            post,
            margin,
            duration_seconds,
            new_pre,
        )
        return new_pre, post
    # Still too big -> also shrink post_roll (floor 0).
    new_post = max(0.0, budget)
    logger.warning(
        "capture window far too large: pre=%.1f post=%.1f margin=%.1f > "
        "duration=%.1f; clamped to pre=0.0 post=%.1f",
        pre,
        post,
        margin,
        duration_seconds,
        new_post,
    )
    return 0.0, new_post


class EncodeWorker(threading.Thread):
    """Drains a bounded queue of encode jobs off the capture thread.

    ``encode_fn(frames, node_id, timestamp, output_dir) -> mp4_path`` is
    run on this worker thread; on success the result is handed to
    ``event_queue.enqueue(node_id=, timestamp=, mp4_path=, metadata=)``.
    The queue is bounded and drop-newest so a slow encoder cannot grow
    memory without bound.
    """

    def __init__(
        self,
        encode_fn: Callable,
        event_queue,
        node_id: str,
        output_dir: str,
        maxsize: int = 2,
    ):
        super().__init__(daemon=True)
        self.name = "EncodeWorker"
        self._encode_fn = encode_fn
        self._event_queue = event_queue
        self._node_id = node_id
        self._output_dir = output_dir
        self._q: "queue.Queue" = queue.Queue(maxsize=maxsize)
        self._stop_event = threading.Event()

    def submit(self, frames: list, timestamp: float, metadata: dict) -> bool:
        """Non-blocking enqueue of an encode job.

        Returns ``True`` if accepted; returns ``False`` and logs a WARNING
        if the bounded queue is full (drop-newest backpressure) so memory
        can't grow unbounded.
        """
        try:
            self._q.put_nowait((frames, timestamp, metadata))
            return True
        except queue.Full:
            logger.warning(
                "encode queue full (size=%d) — dropping event at ts=%.3f",
                self._q.maxsize,
                timestamp,
            )
            return False

    def run(self):
        while not self._stop_event.is_set():
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(job)
            finally:
                self._q.task_done()
        # Leftover jobs (if any) are drained synchronously by stop().

    def _process(self, job):
        frames, timestamp, metadata = job
        try:
            mp4_path = self._encode_fn(
                frames, self._node_id, timestamp, self._output_dir
            )
        except Exception as e:
            logger.error("encode failed for ts=%.3f: %s — event dropped", timestamp, e)
            return
        try:
            self._event_queue.enqueue(
                node_id=self._node_id,
                timestamp=datetime.fromtimestamp(timestamp).isoformat(),
                mp4_path=mp4_path,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(
                "enqueue failed for ts=%.3f (mp4=%s): %s", timestamp, mp4_path, e
            )

    def stop(self, drain: bool = True, timeout: float = 10.0) -> None:
        """Stop the worker.

        To avoid two threads pulling from the queue at once, the run loop
        is signalled and joined FIRST; only then are any leftover queued
        jobs drained synchronously on the caller's thread. This guarantees
        a single consumer of the queue at any instant.
        """
        # 1. Signal the run loop to exit and wait for it, so the worker
        #    thread is no longer pulling from the queue.
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)
        # 2. Now that we are the only consumer, drain any leftovers.
        if drain:
            while True:
                try:
                    job = self._q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._process(job)
                finally:
                    self._q.task_done()
