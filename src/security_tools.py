"""Model-proposed Nmap/Nuclei commands for explicitly authorized local targets.

No shell evaluation, target lists, external templates, NSE scripts or automatic
fallback commands. Planning and execution are separate, digest-bound steps.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import threading
from urllib.parse import urlsplit, urlunsplit

try:
    from .config import load_config
except ImportError:
    from config import load_config

ROOT = Path(__file__).resolve().parents[1]
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16'))
# Tool syntax/capability boundaries; selectable values and defaults live in JSON.
_BOOLEAN_FLAGS = {'nmap': {'-sT', '-sV', '--version-light', '-Pn', '-F', '--reason', '--open'}, 'nuclei': set()}
_VALUE_FLAGS = {'nmap': {'-p': 'ports', '--top-ports': 'count', '-T': 'timing'}, 'nuclei': {'-t': 'templates'}}


class ToolPlanError(ValueError):
    def __init__(self, message, *, response=None):
        super().__init__(message)
        self.response = response


def _time():
    return datetime.now(timezone.utc).isoformat()


def _inside(root, raw):
    path = (Path(root).resolve() / raw).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ToolPlanError('The path must remain inside the project.')
    return path


def _settings(settings):
    return settings if settings is not None else load_config('security_tools')


def _private_ip(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError) as exc:
        raise ToolPlanError('Enter a single local IPv4 literal; names, CIDR networks, and lists are not accepted.') from exc
    if not any(address in network for network in _LOCAL_NETWORKS):
        raise ToolPlanError('The target must belong to a private, loopback, or link-local network.')
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ToolPlanError('Special address not allowed.')
    return str(address)


def validate_target(tool, target):
    if not isinstance(target, str) or not target or any(char.isspace() for char in target):
        raise ToolPlanError('Enter a local target without spaces.')
    if tool == 'nmap':
        return _private_ip(target)
    if tool != 'nuclei':
        raise ToolPlanError('Unknown tool.')
    try:
        parsed = urlsplit(target)
        if (parsed.scheme not in {'http', 'https'} or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise ValueError('Use only the HTTP(S) origin, without credentials, paths, or parameters.')
        host = _private_ip(parsed.hostname)
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError('Port outside the valid range.')
        authority = host + (f':{port}' if port is not None else '')
        return urlunsplit((parsed.scheme, authority, '', '', ''))
    except (ValueError, TypeError) as exc:
        raise ToolPlanError(f'Invalid Nuclei target: {exc}') from exc


def _pinned(root, raw, expected):
    path = _inside(root, raw)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ToolPlanError(f'Local file missing or changed since review: {raw}')
    return path


def tool_status(settings=None):
    settings = _settings(settings)
    return [{'id': name, 'installed': shutil.which(spec['executable']) is not None,
             'executable': shutil.which(spec['executable']),
             'flags': spec['flags'] + list(spec['values'])}
            for name, spec in settings['tools'].items()]


def validate_arguments(tool, args, *, settings=None):
    settings = _settings(settings)
    if tool not in _VALUE_FLAGS or tool not in settings['tools']:
        raise ToolPlanError('Unknown tool.')
    if (not isinstance(args, list) or not args or len(args) > settings['runtime']['max_args']
            or any(not isinstance(value, str) or not value or not value.isascii() for value in args)):
        raise ToolPlanError('The model must return a bounded list of ASCII arguments.')
    spec = settings['tools'][tool]
    if not set(spec['flags']) <= _BOOLEAN_FLAGS[tool] or any(_VALUE_FLAGS[tool].get(key) != value for key, value in spec['values'].items()):
        raise ToolPlanError('The catalog contains options outside the supported capabilities.')
    normalized = []
    seen = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if tool == 'nmap' and re.fullmatch(r'-T[23]', flag):
            # Accept Nmap's usual compact timing spelling, then validate it normally.
            args = args[:index] + ['-T', flag[2:]] + args[index + 1:]
            flag = '-T'
        if flag in seen:
            raise ToolPlanError(f'Repeated option: {flag}')
        seen.add(flag)
        if flag in spec['flags']:
            normalized.append(flag)
            index += 1
            continue
        kind = spec['values'].get(flag)
        if kind is None or index + 1 >= len(args):
            raise ToolPlanError(f'Option not allowed or missing its value: {flag}')
        value = args[index + 1]
        if kind == 'ports':
            if not re.fullmatch(r'[0-9]+(?:,[0-9]+)*', value):
                raise ToolPlanError('Ports must be comma-separated numbers.')
            ports = [int(item) for item in value.split(',')]
            if len(ports) > settings['runtime']['max_ports'] or any(not 1 <= port <= 65535 for port in ports) or len(set(ports)) != len(ports):
                raise ToolPlanError('Port list is invalid, contains duplicates, or exceeds the limit.')
            value = ','.join(map(str, ports))
        elif kind == 'count':
            if not value.isdecimal() or not 1 <= int(value) <= settings['runtime']['max_ports']:
                raise ToolPlanError('Port count outside the configured limit.')
            value = str(int(value))
        elif kind == 'timing':
            if value not in {'2', '3'}:
                raise ToolPlanError('Only timing levels T2 and T3 are allowed.')
        elif kind == 'templates':
            identifiers = value.split(',')
            allowed = {entry['id'] for entry in settings['templates']}
            if not set(identifiers) <= allowed or len(set(identifiers)) != len(identifiers):
                raise ToolPlanError('Select only reviewed local template IDs.')
        normalized.extend([flag, value])
        index += 2
    if tool == 'nmap':
        selections = len(seen & {'-p', '--top-ports', '-F'})
        if selections != 1:
            raise ToolPlanError('The model must select exactly one port list, --top-ports, or -F.')
        if '-F' in seen and settings['runtime']['max_ports'] < 100:
            raise ToolPlanError('The -F mode exceeds the configured port limit.')
        if '--version-light' in seen and '-sV' not in seen:
            raise ToolPlanError('--version-light requires -sV.')
    elif '-t' not in seen:
        raise ToolPlanError('Nuclei requires an explicit selection of reviewed templates.')
    return normalized


def _validate_runtime_policy(tool, tokens):
    """Keep configuration from adding targets, uploads, scripts or arbitrary files."""
    boolean = {'nmap': {'--unprivileged', '-n'},
               'nuclei': {'-disable-update-check', '-no-interactsh', '-disable-redirects', '-no-stdin',
                          '-omit-raw', '-omit-template', '-no-color', '-jsonl'}}[tool]
    valued = {'nmap': {'--max-rate', '--max-retries', '--host-timeout', '-oX'},
              'nuclei': {'-rate-limit', '-concurrency', '-timeout', '-retries', '-type'}}[tool]
    found = set()
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        if flag in found:
            raise ToolPlanError('Execution policy contains repeated options.')
        found.add(flag)
        index += 1
        if flag in boolean:
            continue
        if flag not in valued or index == len(tokens):
            raise ToolPlanError('Execution policy contains an unsupported option.')
        value = tokens[index]
        index += 1
        if flag == '-oX':
            valid = value == '-'
        elif flag == '-type':
            valid = value == 'http'
        elif flag == '--host-timeout':
            valid = bool(re.fullmatch(r'[1-9][0-9]*s', value))
        else:
            valid = value.isdecimal() and int(value) >= (0 if flag in {'--max-retries', '-retries'} else 1)
        if not valid:
            raise ToolPlanError(f'Invalid value in execution policy: {flag}')
    if found != boolean | valued:
        raise ToolPlanError('The policy must retain all scope, limit, and isolation options.')


def build_command(tool, target, args, *, settings=None, root=ROOT):
    settings = _settings(settings)
    target = validate_target(tool, target)
    normalized = validate_arguments(tool, args, settings=settings)
    spec = settings['tools'][tool]
    _validate_runtime_policy(tool, spec['required'])
    executable = shutil.which(spec['executable'])
    if executable is None:
        raise ToolPlanError(f'{tool} was not found on PATH. Install the tool separately.')
    translated = list(normalized)
    if tool == 'nuclei':
        registry = {entry['id']: entry for entry in settings['templates']}
        index = translated.index('-t') + 1
        translated[index] = ','.join(str(_pinned(root, registry[name]['path'], registry[name]['sha256'])) for name in translated[index].split(','))
        config = _pinned(root, settings['runtime']['nuclei_config'], settings['runtime']['nuclei_config_sha256'])
        return [executable, '-config', str(config), *translated, *spec['required'], '-target', target]
    return [executable, *translated, *spec['required'], target]


def _settings_digest(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def plan_digest(plan):
    fields = ('tool', 'target', 'goal', 'args', 'reason', 'argv', 'command', 'settings_sha256')
    return hashlib.sha256(json.dumps({field: plan[field] for field in fields}, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def proposal_from_response(tool, target, goal, response, *, settings=None, root=ROOT, selected_ports=None):
    """Validate generated JSON and bind the exact command shown for review."""
    settings = _settings(settings)
    target = validate_target(tool, target)
    try:
        payload = json.loads(response)
        if not isinstance(payload, dict) or set(payload) != {'args', 'reason'}:
            raise ToolPlanError('The response must contain only args and reason.')
        reason = payload['reason']
        if not isinstance(reason, str) or not reason.strip() or len(reason) > settings['runtime']['max_reason_chars'] or not reason.isprintable():
            raise ToolPlanError('Invalid model rationale.')
        proposed_args = payload['args']
        if selected_ports is not None:
            if (tool != 'nmap' or not isinstance(selected_ports, list) or not selected_ports
                    or any(type(port) is not int or not 1 <= port <= 65535 for port in selected_ports)
                    or len(selected_ports) > settings['runtime']['max_ports']
                    or len(set(selected_ports)) != len(selected_ports)):
                raise ToolPlanError('Invalid application-selected port scope.')
            if not isinstance(proposed_args, list) or any(not isinstance(arg, str) for arg in proposed_args):
                raise ToolPlanError('The model must return an argument list.')
            if any(flag in proposed_args for flag in ('--top-ports', '-F')):
                raise ToolPlanError('Selected-port analysis cannot use --top-ports or -F; remove discovery modes.')
            if '-p' not in proposed_args:
                proposed_args = [*proposed_args, '-p', ','.join(map(str, sorted(selected_ports)))]
        args = validate_arguments(tool, proposed_args, settings=settings)
        if selected_ports is not None:
            actual = [int(port) for port in args[args.index('-p') + 1].split(',')]
            if set(actual) != set(selected_ports):
                raise ToolPlanError('The proposal changed the selected port scope.')
        argv = build_command(tool, target, args, settings=settings, root=root)
    except (ValueError, TypeError, KeyError) as exc:
        raise ToolPlanError(f'Model proposal rejected: {exc}') from exc
    plan = {'status': 'ready', 'tool': tool, 'target': target, 'goal': goal,
            'args': args, 'reason': reason, 'argv': argv, 'settings_sha256': _settings_digest(settings)}
    plan['command'] = shlex.join(argv)
    plan['digest'] = plan_digest(plan)
    return plan


def planning_schema(tool, settings, constraints=None):
    """Constrain model syntax while keeping deterministic policy validation authoritative."""
    item = {'type': 'string', 'minLength': 1}
    if tool == 'nmap' and (constraints or {}).get('port_selection_owned_by_application'):
        tokens = set(settings['tools'][tool]['flags']) - {'-F'}
        if settings['tools'][tool]['values'].get('-T') == 'timing':
            tokens.update(('-T', '2', '3'))
        item['enum'] = sorted(tokens)
    return {'type': 'object', 'additionalProperties': False, 'required': ['args', 'reason'],
            'properties': {'args': {'type': 'array', 'minItems': 1,
                                    'maxItems': settings['runtime']['max_args'], 'items': item},
                           'reason': {'type': 'string', 'minLength': 1,
                                      'maxLength': settings['runtime']['max_reason_chars'],
                                      'pattern': r'^[^\r\n]+$'}}}


def propose(tool, target, goal, *, settings=None, root=ROOT, client=None, database=None, constraints=None, feedback=None):
    """Use local Ollama + RAG to propose arguments, never execute them."""
    settings = _settings(settings)
    target = validate_target(tool, target)
    if not isinstance(goal, str) or not goal.strip() or len(goal) > settings['runtime']['max_goal_chars'] or not goal.isprintable():
        raise ToolPlanError('Enter a short, single-line goal.')
    try:
        from . import rag, scan_intelligence
    except ImportError:
        import rag, scan_intelligence
    path = Path(database or load_config('rag')['database'])
    if not path.is_file():
        raise ToolPlanError('RAG index is required. Run: python vorpal.py knowledge update')
    model_settings = dict(load_config('scan_intelligence'))
    model_settings['response_schema'] = planning_schema(tool, settings, constraints)
    docs = rag.search(tool + ' ' + goal, path, limit=model_settings['rag_results'])
    if not docs:
        raise ToolPlanError('RAG returned no relevant tool documentation.')
    context = [{'id': i, 'source': doc['source'], 'text': doc['text'][:model_settings['max_excerpt_chars']]}
               for i, doc in enumerate(docs, 1)]
    response = (client or scan_intelligence._local_chat)([
        {'role': 'system', 'content': settings['planner']['instruction'] + settings['planner'][tool]},
        {'role': 'user', 'content': json.dumps({'target': target, 'goal': goal, 'references': context,
                                             'constraints': constraints or {}, 'previous_rejection': feedback,
                                             'output_schema': model_settings['response_schema'],
                                             'argument_catalog': {key: settings['tools'][tool][key] for key in ('flags', 'values')}})}
    ], model_settings)
    try:
        plan = proposal_from_response(tool, target, goal, response, settings=settings, root=root,
                                      selected_ports=(constraints or {}).get('ports') if tool == 'nmap' else None)
    except ToolPlanError as exc:
        raise ToolPlanError(str(exc), response=response) from exc
    plan.update(model_response=response, sources=[{k: doc[k] for k in ('source', 'line', 'sha256')} for doc in docs])
    return plan


def _directory(root, settings, kind):
    path = _inside(root, settings['runtime']['reports_dir']) / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_report(value, kind, *, settings=None, root=ROOT):
    settings = _settings(settings)
    identifier = secrets.token_hex(12)
    result = {**value, 'id': identifier, 'created_at': _time()}
    path = _directory(root, settings, kind) / f'{identifier}.json'
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


def read_plan(identifier, *, settings=None, root=ROOT):
    settings = _settings(settings)
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{24}', identifier):
        raise ToolPlanError('Invalid plan identifier.')
    path = _directory(root, settings, 'plans') / f'{identifier}.json'
    if path.is_symlink():
        raise ToolPlanError('The plan cannot be a symbolic link.')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Expected an object')
        return value
    except (OSError, ValueError) as exc:
        raise ToolPlanError(f'Unable to read the plan: {exc}') from exc


def reviewed_command(plan, digest, *, authorized=False, settings=None, root=ROOT):
    settings = _settings(settings)
    if authorized is not True:
        raise ToolPlanError('Confirm authorization to test the local target.')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ToolPlanError('Enter the digest of the reviewed command.')
    try:
        if plan['status'] != 'ready' or not secrets.compare_digest(plan_digest(plan), digest) or plan['digest'] != digest:
            raise ToolPlanError('The plan changed or is not ready. Generate and review a new proposal.')
        if plan['settings_sha256'] != _settings_digest(settings):
            raise ToolPlanError('The configuration changed. Generate and review a new proposal.')
        argv = build_command(plan['tool'], plan['target'], plan['args'], settings=settings, root=root)
        if argv != plan['argv']:
            raise ToolPlanError('The executable or command changed. Generate a new proposal.')
        return argv
    except (KeyError, TypeError) as exc:
        raise ToolPlanError('The saved plan is incomplete or invalid.') from exc


def execute_plan(plan, digest, *, authorized=False, settings=None, root=ROOT):
    settings = _settings(settings)
    argv = reviewed_command(plan, digest, authorized=authorized, settings=settings, root=root)
    print('Executing reviewed command:', shlex.join(argv), flush=True)
    output = ''
    truncated = False
    timed_out = threading.Event()
    started = _time()
    process = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    def deadline():
        try:
            process.wait(timeout=settings['runtime']['timeout_seconds'])
        except subprocess.TimeoutExpired:
            timed_out.set()
            try:
                process.terminate()
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=settings['runtime']['stop_grace_seconds'])
            except subprocess.TimeoutExpired:
                process.kill()
    watcher = threading.Thread(target=deadline, daemon=True)
    watcher.start()
    cancelled = False
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
            combined = output + line
            truncated = truncated or len(combined) > settings['runtime']['max_output_chars']
            output = combined[-settings['runtime']['max_output_chars']:]
        code = process.wait()
    except KeyboardInterrupt:
        cancelled = True
        process.terminate()
        try:
            code = process.wait(timeout=settings['runtime']['stop_grace_seconds'])
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait()
    finally:
        process.stdout.close()
        watcher.join(timeout=settings['runtime']['stop_grace_seconds'] + 1)
    report = write_report({'plan_id': plan['id'], 'tool': plan['tool'], 'target': plan['target'],
                           'argv': argv, 'started_at': started, 'returncode': code, 'output': output,
                           'output_truncated': truncated, 'status': 'cancelled' if cancelled else 'timed_out' if timed_out.is_set() else 'completed' if code == 0 else 'failed'},
                          'executions', settings=settings, root=root)
    print('\nReport:', report['id'], report['status'], flush=True)
    return report


def latest_plan(*, settings=None, root=ROOT):
    settings = _settings(settings)
    directory = _inside(root, settings['runtime']['reports_dir']) / 'plans'
    if not directory.is_dir():
        return None
    paths = sorted(directory.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        if re.fullmatch(r'[a-f0-9]{24}', path.stem) and not path.is_symlink():
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(value, dict):
                    return value
            except (OSError, ValueError):
                continue
    return None


def main():
    settings = load_config('security_tools')
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    planner = modes.add_parser('plan', help='Request a model proposal without executing the tool')
    planner.add_argument('--tool', choices=tuple(settings['tools']), required=True)
    planner.add_argument('--target', required=True)
    planner.add_argument('--goal', required=True)
    planner.add_argument('--rag-db')
    runner = modes.add_parser('run', help='Execute only a previously reviewed plan')
    runner.add_argument('--plan-id', required=True)
    runner.add_argument('--digest', required=True)
    runner.add_argument('--authorized', action='store_true')
    args = parser.parse_args()
    try:
        if args.mode == 'run':
            plan = read_plan(args.plan_id, settings=settings)
            report = execute_plan(plan, args.digest, authorized=args.authorized, settings=settings)
            raise SystemExit(0 if report['status'] == 'completed' else 1)
        target = validate_target(args.tool, args.target)
        if not isinstance(args.goal, str) or not args.goal.strip() or len(args.goal) > settings['runtime']['max_goal_chars'] or not args.goal.isprintable():
            raise ToolPlanError('Invalid goal.')
        try:
            plan = propose(args.tool, target, args.goal, database=args.rag_db, settings=settings)
        except (ToolPlanError, RuntimeError) as exc:
            plan = {'status': 'rejected', 'tool': args.tool, 'target': target, 'goal': args.goal,
                    'error': str(exc), 'model_response': getattr(exc, 'response', None)}
        plan = write_report(plan, 'plans', settings=settings)
        print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
        if plan['status'] != 'ready':
            raise SystemExit(2)
        print('Command for review:', plan['command'], flush=True)
        print(f"Run after review: python src/security_tools.py run --plan-id {plan['id']} --digest {plan['digest']} --authorized", flush=True)
    except (ToolPlanError, OSError, ValueError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
