"""Bounded execution of explicitly selected, policy-validated evidence tools."""
import os
from pathlib import Path
import selectors
import subprocess
import time


def run(argv, stdout_path, stderr_path, *, timeout, max_bytes, cwd):
    """Keep raw evidence on disk; stop on deadline, cancellation or output overflow."""
    total = 0
    deadline = time.monotonic() + timeout
    process = None
    with Path(stdout_path).open('xb') as output, Path(stderr_path).open('xb') as errors:
        try:
            process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       shell=False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, output)
                selector.register(process.stderr, selectors.EVENT_READ, errors)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('Evidence tool exceeded its configured time limit.')
                    for key, _ in selector.select(min(remaining, .2)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        allowed = max(0, max_bytes - total)
                        key.data.write(chunk[:allowed])
                        key.data.flush()
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError('Evidence tool exceeded its configured output limit.')
                try:
                    code = process.wait(timeout=max(.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise ValueError('Evidence tool exceeded its configured time limit.') from exc
            if code:
                raise ValueError(f'Evidence tool exited with status {code}; inspect its saved stderr log.')
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                process.stderr.close()
