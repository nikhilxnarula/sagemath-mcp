"""Locate SageMath and drive a long-lived worker process over JSON lines.

A cold Sage start costs seconds, which would otherwise be paid on every MCP tool
call, so one ``sage -python worker.py`` process is kept alive and reused. On
Windows the process runs inside WSL; elsewhere Sage is invoked directly.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from importlib.resources import as_file, files

DEFAULT_TIMEOUT = 120.0
_PROBE_TIMEOUT = 60.0

# Where Sage commonly lives inside a WSL distro when it is not already on PATH.
_WSL_CANDIDATES = [
    "~/miniconda3/envs/*/bin/sage",
    "~/miniforge3/envs/*/bin/sage",
    "~/mambaforge/envs/*/bin/sage",
    "~/anaconda3/envs/*/bin/sage",
    "/opt/sagemath*/sage",
    "/usr/local/bin/sage",
    "/usr/bin/sage",
]


class SageNotFound(RuntimeError):
    """SageMath could not be located."""


class SageWorkerError(RuntimeError):
    """The worker process failed, timed out, or returned an error."""


@dataclass(frozen=True)
class SageRuntime:
    """How to invoke Sage: an argv prefix, the binary, and whether it is WSL."""

    prefix: tuple[str, ...]
    binary: str
    use_wsl: bool

    def argv(self, *args: str) -> list[str]:
        return [*self.prefix, self.binary, *args]

    def describe(self) -> str:
        return " ".join([*self.prefix, self.binary])


def _wsl_prefix() -> list[str]:
    distro = os.environ.get("SAGE_MCP_WSL_DISTRO")
    return ["wsl", "-d", distro, "-e"] if distro else ["wsl", "-e"]


def _works(runtime: SageRuntime) -> bool:
    try:
        done = subprocess.run(
            runtime.argv("--version"),
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and "SageMath" in (done.stdout + done.stderr)


def _probe_wsl() -> str | None:
    """Ask WSL where Sage is, falling back to common install locations."""
    script = "command -v sage || ls -1 %s 2>/dev/null | head -n 1" % " ".join(
        _WSL_CANDIDATES
    )
    try:
        done = subprocess.run(
            [*_wsl_prefix(), "bash", "-lc", script],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = done.stdout.strip().splitlines()
    return path[0].strip() if path else None


def discover_sage() -> SageRuntime:
    """Find a usable Sage, trying the env var, then PATH, then WSL."""
    attempted = []

    override = os.environ.get("SAGE_MCP_BIN")
    if override:
        # A POSIX-looking path on Windows means "inside WSL".
        forced = os.environ.get("SAGE_MCP_USE_WSL")
        use_wsl = (
            forced not in (None, "", "0")
            if forced is not None
            else (os.name == "nt" and override.startswith("/"))
        )
        runtime = SageRuntime(
            prefix=tuple(_wsl_prefix()) if use_wsl else (),
            binary=override,
            use_wsl=use_wsl,
        )
        if _works(runtime):
            return runtime
        raise SageNotFound(
            "SAGE_MCP_BIN is set to %r but running %r did not produce a working "
            "SageMath. Fix the path or unset SAGE_MCP_BIN to auto-detect."
            % (override, runtime.describe())
        )

    native = shutil.which("sage")
    if native:
        runtime = SageRuntime(prefix=(), binary=native, use_wsl=False)
        if _works(runtime):
            return runtime
        attempted.append(native)

    if os.name == "nt":
        found = _probe_wsl()
        if found:
            runtime = SageRuntime(
                prefix=tuple(_wsl_prefix()), binary=found, use_wsl=True
            )
            if _works(runtime):
                return runtime
            attempted.append("wsl:" + found)
        else:
            attempted.append("wsl (PATH and %s)" % ", ".join(_WSL_CANDIDATES))

    raise SageNotFound(
        "Could not find a working SageMath installation. Tried: %s. "
        "Set SAGE_MCP_BIN to the full path of the sage executable "
        "(a path inside WSL is fine on Windows), and see the README for setup."
        % (", ".join(attempted) or "PATH")
    )


# The worker file must exist on disk for Sage to execute; keep the extraction
# alive for the lifetime of the process.
_resources = contextlib.ExitStack()
atexit.register(_resources.close)


def worker_script_path() -> str:
    return str(_resources.enter_context(as_file(files("sagemath_mcp") / "worker.py")))


def to_sage_path(runtime: SageRuntime, path: str) -> str:
    """Translate a host path for the Sage side (only WSL needs this)."""
    if not runtime.use_wsl:
        return path
    done = subprocess.run(
        [*runtime.prefix, "wslpath", "-a", path],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT,
    )
    if done.returncode != 0:
        raise SageWorkerError("wslpath failed for %r: %s" % (path, done.stderr.strip()))
    return done.stdout.strip()


@dataclass
class SageWorker:
    """A persistent ``sage -python worker.py`` process speaking JSON lines."""

    default_timeout: float = DEFAULT_TIMEOUT
    _runtime: SageRuntime | None = field(default=None, init=False)
    _process: subprocess.Popen | None = field(default=None, init=False)
    _stdout: queue.Queue = field(default_factory=queue.Queue, init=False)
    _stderr: deque = field(default_factory=lambda: deque(maxlen=200), init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _next_id: int = field(default=0, init=False)

    @property
    def runtime(self) -> SageRuntime:
        if self._runtime is None:
            self._runtime = discover_sage()
        return self._runtime

    def _start(self) -> None:
        runtime = self.runtime
        script = to_sage_path(runtime, worker_script_path())
        self._stdout = queue.Queue()
        self._stderr.clear()
        self._process = subprocess.Popen(
            runtime.argv("-python", script),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(
            target=self._drain_stdout, args=(self._process,), daemon=True
        ).start()
        threading.Thread(
            target=self._drain_stderr, args=(self._process,), daemon=True
        ).start()

    def _drain_stdout(self, process: subprocess.Popen) -> None:
        for line in process.stdout:
            self._stdout.put(line)
        self._stdout.put(None)  # EOF sentinel

    def _drain_stderr(self, process: subprocess.Popen) -> None:
        for line in process.stderr:
            self._stderr.append(line.rstrip("\n"))

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _stderr_tail(self, limit: int = 20) -> str:
        tail = [line for line in list(self._stderr)[-limit:] if line.strip()]
        return "\n".join(tail)

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        with contextlib.suppress(Exception):
            process.stdin.close()
        with contextlib.suppress(Exception):
            process.terminate()
            process.wait(timeout=5)
        with contextlib.suppress(Exception):
            process.kill()

    def call(self, fn: str, timeout: float | None = None, **args):
        """Invoke a worker function, restarting the process once if it died."""
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            for attempt in (1, 2):
                if not self._alive():
                    self.close()
                    self._start()
                try:
                    return self._exchange(fn, args, timeout)
                except (BrokenPipeError, ConnectionError, _WorkerGone):
                    self.close()
                    if attempt == 2:
                        raise SageWorkerError(
                            "the SageMath worker keeps dying while handling %r.%s"
                            % (fn, self._suffix())
                        )
        raise AssertionError("unreachable")

    def _suffix(self) -> str:
        tail = self._stderr_tail()
        return ("\nWorker stderr:\n" + tail) if tail else ""

    def _exchange(self, fn: str, args: dict, timeout: float):
        self._next_id += 1
        request_id = self._next_id
        payload = json.dumps({"id": request_id, "fn": fn, "args": args})
        try:
            self._process.stdin.write(payload + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise _WorkerGone(str(exc))

        while True:
            try:
                line = self._stdout.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise SageWorkerError(
                    "SageMath did not answer %r within %.0fs. Large graphs can "
                    "genuinely take this long; retry with a bigger timeout or a "
                    "smaller graph.%s" % (fn, timeout, self._suffix())
                )
            if line is None:
                raise _WorkerGone("worker closed its stdout")
            line = line.strip()
            if not line:
                continue
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray output; the protocol only cares about JSON
            if response.get("id") != request_id:
                continue  # a late reply from a call we already gave up on
            if response.get("ok"):
                return response["result"]
            raise SageWorkerError(response.get("error", "unknown worker error"))


class _WorkerGone(Exception):
    """Internal: the worker process vanished mid-exchange."""


def run_sage_code(code: str, timeout: float = DEFAULT_TIMEOUT,
                  runtime: SageRuntime | None = None) -> dict:
    """Run arbitrary Sage code in a fresh subprocess.

    Deliberately not the shared worker: user code must not be able to corrupt
    worker state or write to the JSON protocol channel.
    """
    runtime = runtime or discover_sage()
    handle, script = tempfile.mkstemp(suffix=".sage")
    os.close(handle)
    try:
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(code)
        try:
            done = subprocess.run(
                runtime.argv(to_sage_path(runtime, script)),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": "timed out after %.0fs" % timeout,
                "returncode": None,
            }
    finally:
        with contextlib.suppress(OSError):
            os.unlink(script)

    return {
        "success": done.returncode == 0,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "returncode": done.returncode,
    }


if __name__ == "__main__":  # tiny smoke test: python -m sagemath_mcp.bridge
    worker = SageWorker()
    try:
        print("runtime:", worker.runtime.describe())
        print("ping:", worker.call("ping"))
    finally:
        worker.close()
        sys.stdout.flush()
