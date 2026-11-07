"""Local Vorpal dashboard. Start with: python src/web.py"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
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
    from .security_tools import latest_plan, read_plan, reviewed_command, tool_status, validate_target as validate_tool_target
except ImportError:
    from config import ConfigError, load_config, validate_config
    from security_tools import latest_plan, read_plan, reviewed_command, tool_status, validate_target as validate_tool_target

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
            if self.closed or self.active():
                raise BusyError("Já existe uma tarefa em execução. Aguarde ou interrompa antes de iniciar outra.")
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
                raise FileNotFoundError("Tarefa não encontrada.")
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
                job["output"] += f"\nNão foi possível executar a tarefa: {exc}\n"
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
                raise ValueError("Esta tarefa não está em execução.")
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
            raise ValueError("Caminho inválido.")
        path = (self.root / raw).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("O caminho deve permanecer dentro do projeto.")
        return path

    def configuration(self, name):
        if not re.fullmatch(r"[a-z_]+", name) or name == "schema":
            raise ValueError("Configuração inválida.")
        path = self.root / "config" / f"{name}.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("Configuração não encontrada.")
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_config(name, value)

    def save_configuration(self, name, value):
        with self.lock, self.jobs.lock:
            if self.jobs.active():
                raise BusyError("Aguarde a tarefa terminar antes de alterar configurações.")
            self.configuration(name)
            validate_config(name, value)
            path = self.root / "config" / f"{name}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            load_config.cache_clear()
        return {"saved": True, "restart_required": name == "web"}

    def profile(self, identifier):
        profiles = self.settings["profiles"]
        profile = next((profile for profile in profiles if profile["id"] == identifier), None)
        if profile is None:
            raise ValueError("Selecione um perfil de corpus válido.")
        self.path(profile["corpus"])
        self.path(profile["out"])
        return profile

    def checkpoints(self):
        train = self.configuration("train")
        latest = self.path(train["policy"]["checkpoint"])
        return {"latest": latest, "best": latest.with_name(train["runtime"]["best_checkpoint_name"])}

    def _number(self, params, key, default, maximum, *, minimum=1, integer=True):
        value = params.get(key, default)
        if (isinstance(value, bool) or (type(value) is not int if integer else type(value) not in (int, float))
                or not math.isfinite(value) or not minimum <= value <= maximum):
            raise ValueError(f"{key}: informe um {'inteiro' if integer else 'número'} entre {minimum} e {maximum}.")
        return value

    def command(self, action, params):
        allowed = {
            "generate": {"prompt", "max_new", "temperature", "threads", "checkpoint"},
            "train": {"steps", "batch", "threads", "profile", "mode"},
            "evaluate": {"samples", "threads", "checkpoint", "profile"},
            "benchmark": {"threads", "checkpoint"},
            "prepare": {"profile"}, "import": {"profile"},
            "tool_plan": {"tool", "target", "goal", "threads", "checkpoint"},
            "tool_run": {"plan_id", "digest", "authorized"},
        }
        if action not in allowed or not isinstance(params, dict) or params.keys() - allowed[action]:
            raise ValueError("Ação ou parâmetros inválidos.")
        if action in {"tool_plan", "tool_run"}:
            settings = self.configuration("security_tools")
            command = [sys.executable, "-u", str(self.root / "src/security_tools.py")]
            if action == "tool_run":
                plan = read_plan(params.get("plan_id"), settings=settings, root=self.root)
                reviewed_command(plan, params.get("digest"), authorized=params.get("authorized"), settings=settings, root=self.root)
                return command + ["run", "--plan-id", params["plan_id"], "--digest", params["digest"], "--authorized"]
            tool = params.get("tool")
            if tool not in settings["tools"]:
                raise ValueError("Selecione Nmap ou Nuclei.")
            target = validate_tool_target(tool, params.get("target"))
            goal = params.get("goal")
            if not isinstance(goal, str) or not goal.strip() or not goal.isprintable() or len(goal) > settings["runtime"]["max_goal_chars"]:
                raise ValueError("Informe um objetivo curto, em uma única linha.")
            checkpoint = self.checkpoints().get(params.get("checkpoint", "latest"))
            if checkpoint is None or not checkpoint.is_file():
                raise ValueError("Um checkpoint treinado é obrigatório para planejar comandos.")
            threads = self._number(params, "threads", settings["cli"]["threads"], self.settings["limits"]["threads"])
            return command + ["plan", "--tool", tool, "--target", target, "--goal", goal,
                              "--checkpoint", str(checkpoint), "--threads", str(threads)]
        section = {"generate": "generate", "train": "train", "evaluate": "evaluate", "benchmark": "benchmark",
                   "prepare": "data", "import": "import_dataset"}[action]
        defaults = self.configuration(section)["cli"]
        limits = self.settings["limits"]
        command = [sys.executable, "-u", str(self.root / "src" / f"{section}.py")]
        if action in {"generate", "train", "evaluate", "benchmark"}:
            command += ["--threads", str(self._number(params, "threads", defaults["threads"], limits["threads"]))]
        if action in {"generate", "evaluate", "benchmark"}:
            checkpoint = self.checkpoints().get(params.get("checkpoint", "latest"))
            if checkpoint is None or not checkpoint.is_file():
                raise ValueError("Checkpoint não encontrado. Treine o modelo ou escolha um checkpoint disponível.")
            command += ["--checkpoint", str(checkpoint)]
        if action == "generate":
            prompt = params.get("prompt", defaults["prompt"])
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > limits["prompt_chars"]:
                raise ValueError(f"Informe um prompt com até {limits['prompt_chars']} caracteres.")
            command += ["--prompt", prompt, "--max-new", str(self._number(params, "max_new", defaults["max_new"], limits["max_new"])),
                        "--temperature", str(self._number(params, "temperature", defaults["temperature"], 2, minimum=0.01, integer=False))]
        if action in {"train", "evaluate", "prepare", "import"}:
            profile = self.profile(params.get("profile", self.settings["profiles"][0]["id"]))
        if action == "train":
            mode = params.get("mode", "new")
            if mode not in {"new", "replay"}:
                raise ValueError("Modo de treinamento inválido.")
            if mode == "replay":
                train = self.configuration("train")
                if not self.checkpoints()["latest"].is_file() or not self.path(train["policy"]["metadata"]).is_file():
                    raise ValueError("Repetir o corpus requer checkpoint e metadados existentes.")
                command += ["--replay"]
            command += ["--steps", str(self._number(params, "steps", defaults["steps"], limits["steps"])),
                        "--batch", str(self._number(params, "batch", defaults["batch"], limits["batch"])),
                        "--corpus", profile["corpus"], "--out", profile["out"]]
        if action == "evaluate":
            command += ["--samples", str(self._number(params, "samples", defaults["samples"], limits["samples"])),
                        "--data", str(self.path(profile["out"]) / "valid.bin")]
        if action == "prepare":
            command += ["--corpus", profile["corpus"], "--out", profile["out"]]
        if action == "import":
            directory = self.path(profile["corpus"]) / self.configuration("train")["runtime"]["imported_subdir"]
            command += ["--directory", profile["corpus"], "--out", str(directory)]
        if action == "benchmark":
            self.path(defaults["output"])
        return command

    def start(self, action, params):
        with self.lock:
            return self.jobs.start(action, self.command(action, params))

    def state(self):
        train = self.configuration("train")
        checkpoint_paths = self.checkpoints()
        metadata = read_json(self.path(train["policy"]["metadata"]))
        best = read_json(checkpoint_paths["latest"].with_name(train["runtime"]["best_metadata_name"]))
        profiles = []
        for profile in self.settings["profiles"]:
            profile = {**profile, "manifest": read_json(self.path(profile["out"]) / "manifest.json")}
            profiles.append(profile)
        corpus = self.path(self.settings["profiles"][0]["corpus"])
        pdfs = []
        if corpus.is_dir():
            for path in sorted(corpus.rglob("*.pdf")):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(self.root):
                    pdfs.append({"name": path.name, "path": str(path.relative_to(self.root)), "bytes": path.stat().st_size})
        metrics_path = checkpoint_paths["latest"].with_name(train["runtime"]["metrics_name"])
        metrics = []
        if metrics_path.is_file():
            with metrics_path.open(encoding="utf-8") as stream:
                for line in deque(stream, maxlen=self.settings["runtime"]["metrics_limit"]):
                    try:
                        item = json.loads(line)
                        if isinstance(item, dict) and all(isinstance(item.get(key), (int, float)) and math.isfinite(item[key]) for key in ("step", "train_loss", "validation_loss")):
                            metrics.append(item)
                    except ValueError:
                        continue
        benchmark = read_json(self.path(self.configuration("benchmark")["cli"]["output"]))
        evaluation = None
        with self.jobs.lock:
            for job in reversed(self.jobs.jobs):
                if job["action"] == "evaluate" and job["status"] == "completed":
                    match = re.search(r"Validation loss=([\d.eE+-]+); byte-level perplexity=([\d.eE+-]+)", job["output"])
                    if match:
                        loss, perplexity = map(float, match.groups())
                        if math.isfinite(loss) and math.isfinite(perplexity):
                            command = job["command"]
                            evaluation = {"loss": loss, "perplexity": perplexity, "finished_at": job["finished_at"],
                                          "dataset": command[command.index("--data") + 1] if "--data" in command else "",
                                          "samples": command[command.index("--samples") + 1] if "--samples" in command else None}
                            break
        tool_settings = self.configuration("security_tools")
        return {"token": self.token, "poll_ms": self.settings["runtime"]["poll_ms"], "metadata": metadata,
                "best": best, "checkpoints": [{"id": name, "exists": path.is_file(), "path": str(path.relative_to(self.root))} for name, path in checkpoint_paths.items()],
                "profiles": profiles, "pdfs": pdfs[:self.settings["runtime"]["file_limit"]], "pdf_count": len(pdfs),
                "metrics": metrics, "benchmark": benchmark, "evaluation": evaluation, "jobs": self.jobs.snapshot(),
                "tools": tool_status(tool_settings), "tool_plan": latest_plan(settings=tool_settings, root=self.root),
                "defaults": {name: self.configuration(name)["cli"] for name in ("generate", "train", "evaluate", "benchmark", "security_tools")},
                "limits": self.settings["limits"], "configurations": sorted(path.stem for path in (self.root / "config").glob("*.json") if path.stem != "schema")}


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
            raise PermissionError("Host não autorizado. Acesse pelo endereço local do painel.")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            raise PermissionError("Origem não autorizada.")
        if mutation and not secrets.compare_digest(self.headers.get("X-Vorpal-Token", ""), self.server.app.token):
            raise PermissionError("Sessão inválida. Atualize a página e tente novamente.")

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
            raise ValueError("Envie os dados como application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transferência não suportada.")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= self.server.app.settings["runtime"]["max_request_bytes"]:
            self.close_connection = True
            raise ValueError("Tamanho da requisição inválido.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Informe um objeto JSON.")
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
                else:
                    self._send(404, {"error": "Rota não encontrada."})
            elif path == "/api/state":
                self._send(200, app.state())
            elif re.fullmatch(r"/api/jobs/[a-f0-9]{24}", path):
                self._send(200, app.jobs.get(path.rsplit("/", 1)[1]))
            elif path == "/api/config":
                name = parse_qs(url.query).get("name", [""])[0]
                self._send(200, {"name": name, "value": app.configuration(name)})
            elif path == "/files":
                raw = parse_qs(url.query).get("path", [""])[0]
                pdf = app.path(raw)
                corpus = app.path(app.settings["profiles"][0]["corpus"])
                if not pdf.is_relative_to(corpus) or pdf.suffix.lower() != ".pdf" or not pdf.is_file():
                    raise FileNotFoundError("PDF não encontrado.")
                self._send(200, pdf.read_bytes(), "application/pdf", {"Content-Disposition": f"inline; filename*=UTF-8''{quote(pdf.name)}"})
            elif path in {"/", "/app.js", "/styles.css", "/favicon.svg"}:
                filename = "index.html" if path == "/" else path[1:]
                types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}
                self._send(200, (app.root / "web" / filename).read_bytes(), types[filename])
            else:
                self._send(404, {"error": "Rota não encontrada."})
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
            self._send(500, {"error": f"Erro local: {exc}"})


def main():
    settings = load_config("web")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=settings["server"]["port"])
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port deve estar entre 1 e 65535")
    app = Application(settings=settings)
    server = Server((settings["server"]["host"], args.port), app)
    print(f"Vorpal Web: http://127.0.0.1:{server.server_address[1]}", flush=True)
    print("Painel local · TensorFlow CPU · Ctrl+C para encerrar", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
