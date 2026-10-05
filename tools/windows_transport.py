"""Windows pipe I/O for the local runner; official scoring/engine stay untouched."""
import json
import queue
import subprocess
import threading

def transport_class():
    from project_platform.transport import (
        JsonlTransport, ExecutionError, GlobalDeadlineExpired,
        MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    )

    class WindowsTransport(JsonlTransport):
        def start(self):
            if self.process is not None:
                return
            self.process = subprocess.Popen(
                self.command, cwd=self.cwd, env=self.environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self._chunks = queue.Queue()
            self._reader = threading.Thread(target=self._drain_log, args=(self.process.stderr,), daemon=True)
            self._reader.start()
            def pump():
                while True:
                    try:
                        chunk = self.process.stdout.read(65536)
                    except (OSError, ValueError, AttributeError):
                        chunk = b''
                    self._chunks.put(chunk)
                    if not chunk:
                        return
            self._stdout_reader = threading.Thread(target=pump, daemon=True)
            self._stdout_reader.start()

        def send(self, message, deadline, *, limit=MAX_REQUEST_BYTES):
            self.start()
            data = json.dumps(message, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8') + b'\n'
            if len(data) > limit:
                raise ExecutionError('Public protocol request exceeds the size limit.')
            failures = []
            stream = self.process.stdin
            def write():
                try:
                    remaining = memoryview(data)
                    while remaining:
                        count = stream.write(remaining)
                        if not count:
                            raise BrokenPipeError('incomplete write')
                        remaining = remaining[count:]
                    stream.flush()
                except (OSError, ValueError) as exc:
                    failures.append(exc)
            worker = threading.Thread(target=write, daemon=True)
            worker.start()
            worker.join(self._remaining(deadline))
            if worker.is_alive():
                self.close(force=True)
                raise GlobalDeadlineExpired()
            if failures:
                raise ExecutionError('Project exited before reading a request.') from failures[0]

        def receive(self, deadline):
            self.start()
            while b'\n' not in self._buffer:
                if len(self._buffer) > MAX_RESPONSE_BYTES:
                    raise ExecutionError('Project response exceeds the size limit.')
                try:
                    chunk = self._chunks.get(timeout=self._remaining(deadline))
                except queue.Empty:
                    self.close(force=True)
                    raise GlobalDeadlineExpired()
                if not chunk:
                    raise ExecutionError('Project exited without a complete response.')
                self._buffer.extend(chunk)
            line, rest = self._buffer.split(b'\n', 1)
            self._buffer = bytearray(rest)
            if len(line) > MAX_RESPONSE_BYTES:
                raise ExecutionError('Project response exceeds the size limit.')
            try:
                def bad_constant(value):
                    raise ValueError('non-finite JSON constant')
                payload = json.loads(line, parse_constant=bad_constant)
            except (ValueError, UnicodeError) as exc:
                raise ExecutionError('Project stdout must contain JSON-Lines responses.') from exc
            if not isinstance(payload, dict):
                raise ExecutionError('Project response must be a JSON object.')
            return payload

    return WindowsTransport
