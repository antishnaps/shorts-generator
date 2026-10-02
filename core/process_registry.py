#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import threading
import time
import json
from typing import Any, Iterable, Optional


class ProcessRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._processes = {}

    def register(self, process: subprocess.Popen, label: str = "process"):
        with self._lock:
            self._processes[process.pid] = {
                'process': process,
                'label': label,
                'started_at': time.time(),
            }

    def unregister(self, process: subprocess.Popen):
        with self._lock:
            self._processes.pop(process.pid, None)

    def active_count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._processes)

    def terminate_all(self, timeout: float = 3.0) -> int:
        timeout = max(0.0, float(timeout or 0.0))
        deadline = time.monotonic() + timeout
        # A failed transition render can immediately launch its simple-concat
        # fallback.  Keep observing the registry for a short quiet window so
        # Stop also catches that replacement instead of returning after a stale
        # one-time snapshot.
        quiet_window = min(0.5, max(0.05, timeout / 4.0))
        quiet_since = None
        signalled = set()

        while True:
            with self._lock:
                self._prune_locked()
                items = list(self._processes.values())

            now = time.monotonic()
            alive = [item for item in items if item['process'].poll() is None]
            if alive:
                quiet_since = None
                for item in alive:
                    process = item['process']
                    process_key = id(process)
                    if process_key in signalled:
                        continue
                    signalled.add(process_key)
                    try:
                        process.terminate()
                    except Exception:
                        pass
            else:
                if quiet_since is None:
                    quiet_since = now
                if now - quiet_since >= quiet_window:
                    with self._lock:
                        self._prune_locked()
                    return 0

            if now >= deadline:
                break
            time.sleep(min(0.02, max(0.0, deadline - now)))

        # Include any process registered while the cooperative drain was in
        # progress, then force-stop only children that still remain alive.
        with self._lock:
            self._prune_locked()
            items = list(self._processes.values())

        killed = 0
        for item in items:
            process = item['process']
            if process.poll() is None:
                try:
                    process.kill()
                    killed += 1
                except Exception:
                    pass

        # Reap force-killed children as well.  Without this final bounded wait,
        # a timed-out FFmpeg may remain visible as an active/zombie process until
        # the rendering thread eventually returns from communicate().
        kill_deadline = time.monotonic() + max(0.1, min(1.0, timeout))
        for item in items:
            process = item['process']
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.0, kill_deadline - time.monotonic()))
                except Exception:
                    pass

        with self._lock:
            self._prune_locked()
        return killed

    def _prune_locked(self):
        dead = [pid for pid, item in self._processes.items() if item['process'].poll() is not None]
        for pid in dead:
            self._processes.pop(pid, None)


_registry = ProcessRegistry()


def get_process_registry() -> ProcessRegistry:
    return _registry


def run_registered(
    cmd: Iterable[str],
    label: str = "process",
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run a child process while exposing it to the application Stop action.

    The accepted convenience arguments intentionally mirror the small subset of
    :func:`subprocess.run` used by production rendering paths.  Centralising the
    Popen lifecycle here guarantees registration, timeout cleanup and reaping.
    """
    capture_output = bool(kwargs.pop("capture_output", False))
    check = bool(kwargs.pop("check", False))
    input_data = kwargs.pop("input", None)

    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input_data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin argument may not be used with input")
        kwargs["stdin"] = subprocess.PIPE

    process = subprocess.Popen(cmd, **kwargs)
    _registry.register(process, label)
    try:
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
        completed = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        if check and process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, cmd, output=stdout, stderr=stderr
            )
        return completed
    except subprocess.TimeoutExpired as timeout_error:
        # Give cooperative programs a brief chance to flush and exit before
        # escalating.  communicate() after kill both drains pipes and reaps the
        # child, so a timeout cannot leave an orphaned FFmpeg/ffprobe process.
        try:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            cmd,
            timeout,
            output=stdout if stdout is not None else timeout_error.output,
            stderr=stderr if stderr is not None else timeout_error.stderr,
        )
    finally:
        _registry.unregister(process)


def probe_registered(
    filename: str,
    cmd: str = "ffprobe",
    timeout: Optional[float] = 30,
    label: str = "ffprobe",
    **kwargs: Any,
) -> dict:
    """Cancellation-aware equivalent of :func:`ffmpeg.probe`."""
    from ffmpeg import Error
    from ffmpeg._utils import convert_kwargs_to_cmd_line_args

    command = [cmd, "-show_format", "-show_streams", "-of", "json"]
    command += convert_kwargs_to_cmd_line_args(kwargs)
    command.append(str(filename))
    result = run_registered(
        command,
        label=label,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise Error("ffprobe", result.stdout, result.stderr)

    output = result.stdout or b"{}"
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return json.loads(output)
