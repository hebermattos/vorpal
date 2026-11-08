"""Local Vorpal dashboard. Start with: python src/web.py"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
from urllib.parse import parse_qs, quote, urlsplit

try:
    from .config import ConfigError, load_config, validate_config
    from .security_tools import read_plan, reviewed_command, validate_target as validate_tool_target
except ImportError:
    from config import ConfigError, load_config, validate_config
    from security_tools import read_plan, reviewed_command, validate_target as validate_tool_target

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class BusyError(ValueError):
    pass


class JobManager:
    """One CPU task at a time, with bounded in-memory output and full disk logs."""
    def __init__(self, root, settings):
        self.root = root
        self.settings = settings
        self.lock = threading.RLock()
        self.jobs = []
        self.process = None
        self.worker = None
        self.closed = False
        self.chat_active = False
        self.directory = (root / settings["log_dir"]).resolve()
        if not self.directory.is_relative_to(root.resolve()):
            raise ValueError("The job log directory must stay inside the project")
        self.directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)[-settings["history_limit"]:]:
            job = read_json(path)
            if (not isinstance(job, dict) or not re.fullmatch(r"[a-f0-9]{24}", str(job.get("id", "")))
                    or not {"action", "command", "status", "created_at", "finished_at", "cancel_requested"} <= job.keys()):
                continue
            if job.get("status") == "running":
                job.update(status="interrupted", finished_at=now())
            job["output"] = self._read_log(job["id"])
            self.jobs.append(job)

    def _read_log(self, identifier):
        path = self.directory / f"{identifier}.log"
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - self.settings["max_output_chars"] * 4))
                return stream.read().decode("utf-8", errors="replace")[-self.settings["max_output_chars"]:]
        except OSError:
            return ""

    def _persist(self, job):
        path = self.directory / f"{job['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({key: value for key, value in job.items() if key != "output"}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def active(self):
        return next((job for job in self.jobs if job["status"] == "running"), None)

    def start(self, action, command):
        with self.lock:
            if self.closed or self.active() or self.chat_active:
                raise BusyError("A job is already running. Wait or cancel it before starting another.")
            job = {"id": secrets.token_hex(12), "action": action, "command": command,
                   "status": "running", "created_at": now(), "finished_at": None,
                   "returncode": None, "cancel_requested": False, "output": ""}
            self.jobs.append(job)
            self.jobs = self.jobs[-self.settings["history_limit"]:]
            try:
                self._persist(job)
            except OSError:
                self.jobs.remove(job)
                raise
            self.worker = threading.Thread(target=self._run, args=(job,), daemon=True)
            self.worker.start()
            return self._copy(job)

    def _copy(self, job, output=True):
        return {key: value for key, value in job.items() if output or key != "output"}

    def snapshot(self):
        with self.lock:
            return [self._copy(job, output=False) for job in reversed(self.jobs)]

    def get(self, identifier):
        with self.lock:
            job = next((job for job in self.jobs if job["id"] == identifier), None)
            if job is None:
                raise FileNotFoundError("Job not found.")
            return self._copy(job)

    def _run(self, job):
        code = None
        try:
            with (self.directory / f"{job['id']}.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(job["command"], cwd=self.root, stdout=subprocess.PIPE,
                                           stdin=subprocess.DEVNULL,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                           errors="replace", bufsize=1, start_new_session=os.name == "posix")
                with self.lock:
                    self.process = process
                    if job["cancel_requested"]:
                        self._signal_stop(process)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    with self.lock:
                        job["output"] = (job["output"] + line)[-self.settings["max_output_chars"]:]
                process.stdout.close()
                code = process.wait()
        except (OSError, ValueError) as exc:
            with self.lock:
                job["output"] += f"\nUnable to execute the job: {exc}\n"
        finally:
            with self.lock:
                job.update(returncode=code, finished_at=now(),
                           status="cancelled" if job["cancel_requested"] else "completed" if code == 0 else "failed")
                self.process = None
                self._persist(job)

    def _signal_stop(self, process):
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGINT)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        def escalate():
            try:
                process.wait(timeout=self.settings["stop_grace_seconds"])
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
        threading.Thread(target=escalate, daemon=True).start()

    def cancel(self, identifier):
        with self.lock:
            job = self.active()
            if job is None or job["id"] != identifier:
                raise ValueError("This job is not running.")
            job["cancel_requested"] = True
            if self.process is not None:
                self._signal_stop(self.process)
            return self._copy(job)

    def close(self):
        with self.lock:
            self.closed = True
            job = self.active()
            if job:
                self.cancel(job["id"])
            process = self.process
            worker = self.worker
        if process:
            try:
                process.wait(timeout=self.settings["stop_grace_seconds"] + 1)
            except subprocess.TimeoutExpired:
                process.kill()
        if worker is not None:
            worker.join(timeout=self.settings["stop_grace_seconds"] + 2)


class Application:
    def __init__(self, root=ROOT, settings=None):
        self.root = Path(root).resolve()
        self.settings = settings or load_config("web")
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.jobs = JobManager(self.root, self.settings["runtime"])

    def path(self, raw):
        if not isinstance(raw, str):
            raise ValueError("Invalid path.")
        path = (self.root / raw).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("The path must remain inside the project.")
        return path

    def configuration(self, name):
        if not re.fullmatch(r"[a-z_]+", name) or name == "schema":
            raise ValueError("Invalid configuration.")
        path = self.root / "config" / f"{name}.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("Configuration not found.")
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_config(name, value)

    def save_configuration(self, name, value):
        with self.lock, self.jobs.lock:
            if self.jobs.active() or self.jobs.chat_active:
                raise BusyError("Wait for the job to finish before changing settings.")
            self.configuration(name)
            validate_config(name, value)
            path = self.root / "config" / f"{name}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            load_config.cache_clear()
        return {"saved": True, "restart_required": name == "web"}

    def command(self, action, params):
        allowed = {
            'analyze_ip': {'target', 'authorized', 'ports', 'tool', 'goal', 'plan_id', 'digest'},
            'knowledge_update': {'import_sources'},
            'tool_plan': {'tool', 'target', 'goal'},
            'tool_run': {'plan_id', 'digest', 'authorized'},
        }
        if action not in allowed or not isinstance(params, dict) or params.keys() - allowed[action]:
            raise ValueError('Invalid action or parameters.')
        if action == 'analyze_ip':
            if params.get('authorized') is not True:
                raise ValueError('Explicit authorization is required for this target.')
            target = validate_tool_target('nmap', params.get('target'))
            command = [sys.executable, '-u', str(self.root / 'src/assistant.py'),
                       'analyze', '--target', target, '--authorized']
            if 'tool' in params:
                if params['tool'] not in {'tcp', 'nmap'}:
                    raise ValueError('Select tcp or nmap.')
                command += ['--tool', params['tool']]
            for key in ('goal', 'plan_id', 'digest'):
                if key in params:
                    value = params[key]
                    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                        raise ValueError(f'Invalid {key}.')
                    command += ['--' + key.replace('_', '-'), value]
            if params.get('ports'):
                try:
                    from .assistant import ports_from_text
                except ImportError:
                    from assistant import ports_from_text
                command += ['--ports', ','.join(map(str, ports_from_text(params['ports'])))]
            return command
        if action == 'knowledge_update':
            if type(params.get('import_sources', False)) is not bool:
                raise ValueError('import_sources must be a boolean.')
            return [sys.executable, '-u', str(self.root / 'vorpal.py'), 'knowledge', 'update'] + (
                ['--import-sources'] if params.get('import_sources') else [])
        settings = self.configuration('security_tools')
        command = [sys.executable, '-u', str(self.root / 'src/security_tools.py')]
        if action == 'tool_run':
            plan = read_plan(params.get('plan_id'), settings=settings, root=self.root)
            reviewed_command(plan, params.get('digest'), authorized=params.get('authorized'), settings=settings, root=self.root)
            return command + ['run', '--plan-id', params['plan_id'], '--digest', params['digest'], '--authorized']
        tool = params.get('tool')
        if tool not in settings['tools']:
            raise ValueError('Select Nmap or Nuclei.')
        target = validate_tool_target(tool, params.get('target'))
        goal = params.get('goal')
        if not isinstance(goal, str) or not goal.strip() or not goal.isprintable() or len(goal) > settings['runtime']['max_goal_chars']:
            raise ValueError('Enter a short, single-line goal.')
        return command + ['plan', '--tool', tool, '--target', target, '--goal', goal]

    def start(self, action, params):
        with self.lock:
            return self.jobs.start(action, self.command(action, params))

    def chat(self, body):
        if body.keys() - {'message', 'session_id', 'report_id'}:
            raise ValueError('Invalid chat parameters.')
        with self.jobs.lock:
            if self.jobs.closed or self.jobs.active() or self.jobs.chat_active:
                raise BusyError('Wait for the running request to finish before sending a chat message.')
            self.jobs.chat_active = True
        try:
            try:
                from .assistant import chat
            except ImportError:
                from assistant import chat
            return chat(body.get('message'), session_id=body.get('session_id'),
                        report_id=body.get('report_id'), root=self.root)
        finally:
            with self.jobs.lock:
                self.jobs.chat_active = False

    def state(self):
        inference = self.configuration('scan_intelligence')
        return {'token': self.token, 'poll_ms': self.settings['runtime']['poll_ms'],
                'default_tool': self.configuration('assistant')['default_tool'],
                'inference': {key: inference[key] for key in ('host', 'port', 'model')},
                'jobs': self.jobs.snapshot(),
                'configurations': sorted(path.stem for path in (self.root / 'config').glob('*.json') if path.stem != 'schema')}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _check_origin(self, mutation=False):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update({"127.0.0.1", "localhost"})
        if self.headers.get("Host") not in hosts:
            raise PermissionError("Unauthorized host. Use the dashboard's local address.")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            raise PermissionError("Unauthorized origin.")
        if mutation and not secrets.compare_digest(self.headers.get("X-Vorpal-Token", ""), self.server.app.token):
            raise PermissionError("Invalid session. Refresh the page and try again.")

    def _send(self, code, payload, content_type="application/json; charset=utf-8", extra=None):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    def _body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Send data as application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Unsupported transfer encoding.")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= self.server.app.settings["runtime"]["max_request_bytes"]:
            self.close_connection = True
            raise ValueError("Invalid request size.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Enter a JSON object.")
        return value

    def _dispatch(self, mutation):
        try:
            self._check_origin(mutation)
            app = self.server.app
            url = urlsplit(self.path)
            path = url.path
            if mutation:
                body = self._body()
                if path == "/api/jobs":
                    result = app.start(body.get("action"), body.get("params", {}))
                    self._send(202, result)
                elif re.fullmatch(r"/api/jobs/[a-f0-9]{24}/cancel", path):
                    self._send(200, app.jobs.cancel(path.split("/")[3]))
                elif path == "/api/config":
                    self._send(200, app.save_configuration(body.get("name", ""), body.get("value")))
                elif path == '/api/chat':
                    self._send(200, app.chat(body))
                else:
                    self._send(404, {"error": "Route not found."})
            elif path == "/api/state":
                state = app.state()
                if getattr(self.server, "reload_enabled", False):
                    state["dev_revision"] = [app.token, source_revision(app.root, frontend=True)]
                self._send(200, state)
            elif re.fullmatch(r"/api/jobs/[a-f0-9]{24}", path):
                self._send(200, app.jobs.get(path.rsplit("/", 1)[1]))
            elif path == "/api/config":
                name = parse_qs(url.query).get("name", [""])[0]
                self._send(200, {"name": name, "value": app.configuration(name)})
            elif path == "/files":
                raw = parse_qs(url.query).get("path", [""])[0]
                pdf = app.path(raw)
                corpus = app.path(app.configuration("knowledge")["corpus"])
                if not pdf.is_relative_to(corpus) or pdf.suffix.lower() != ".pdf" or not pdf.is_file():
                    raise FileNotFoundError("PDF not found.")
                self._send(200, pdf.read_bytes(), "application/pdf", {"Content-Disposition": f"inline; filename*=UTF-8''{quote(pdf.name)}"})
            elif path in {"/", "/app.js", "/styles.css", "/favicon.svg"}:
                filename = "index.html" if path == "/" else path[1:]
                types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}
                self._send(200, (app.root / "web" / filename).read_bytes(), types[filename])
            else:
                self._send(404, {"error": "Route not found."})
        except PermissionError as exc:
            self.close_connection = True
            self._send(403, {"error": str(exc)})
        except BusyError as exc:
            self._send(409, {"error": str(exc)})
        except FileNotFoundError as exc:
            self._send(404, {"error": str(exc)})
        except (ConfigError, ValueError, TypeError, KeyError, OverflowError) as exc:
            self._send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as exc:
            self._send(500, {"error": f"Local error: {exc}"})


def source_revision(root, *, frontend=False):
    """Watch source files only; model, dataset and log writes do not reload."""
    folders = (("web", "*"),) if frontend else (("src", "*.py"), ("config", "*.json"))
    files = []
    for folder, pattern in folders:
        for path in sorted((root / folder).rglob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                files.append((str(path.relative_to(root)), stat.st_mtime_ns, stat.st_size))
    return files


def watch_sources(server, stopped, restart):
    previous = source_revision(server.app.root)
    while not stopped.wait(0.5):
        if source_revision(server.app.root) == previous:
            continue
        with server.app.jobs.lock:
            if server.app.jobs.active() or server.app.jobs.chat_active:
                continue
            server.app.jobs.closed = True
        print("Source changed; restarting the dashboard…", flush=True)
        restart.set()
        server.shutdown()
        return


def main():
    settings = load_config("web")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=settings["server"]["port"])
    parser.add_argument("--reload", action="store_true", help="Reload the browser on frontend edits and restart idle backend on source/config edits.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port deve estar entre 1 e 65535")
    app = Application(settings=settings)
    server = Server((settings["server"]["host"], args.port), app)
    server.reload_enabled = args.reload
    stopped, restart = threading.Event(), threading.Event()
    if args.reload:
        threading.Thread(target=watch_sources, args=(server, stopped, restart), daemon=True).start()
        print("Development reload enabled (backend reload waits for active jobs).", flush=True)
    print(f"Vorpal Web: http://127.0.0.1:{server.server_address[1]}", flush=True)
    print("Local dashboard · Ollama + RAG security assistant · Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        app.jobs.close()
        server.server_close()
    if restart.is_set():
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    main()
