"""Ollama-compatible security chat and explicitly authorized TCP observations."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import shlex
import socket
import sys
import threading

try:
    from .config import load_config
    from . import collectors, rag, scan_intelligence, security_tools
except ImportError:
    from config import load_config
    import collectors, rag, scan_intelligence, security_tools

ROOT = Path(__file__).resolve().parents[1]
CHAT_LOCK = threading.Lock()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{24}', value):
        raise ValueError('Invalid conversation or report ID.')
    return value


def log_directory(root=ROOT):
    root = Path(root).resolve()
    path = root / load_config('assistant')['log_dir']
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Assistant logs must remain inside the project.')
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_record(path):
    settings = load_config('assistant')
    limit = settings['max_report_bytes'] if path.name.startswith('analysis_') else settings['max_session_bytes']
    return json.loads(scan_intelligence._read(path, limit))


def save_record(path, value):
    if path.is_symlink():
        raise ValueError('Assistant log must not be a symbolic link.')
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    settings = load_config('assistant')
    limit = settings['max_report_bytes'] if 'observations' in value and 'report_id' in value else settings['max_session_bytes']
    if len(payload.encode('utf-8')) > limit:
        raise ValueError('Assistant record exceeds the configured size limit.')
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    temporary.write_text(payload, encoding='utf-8')
    temporary.replace(path)


def preflight(database=None):
    path = Path(database or load_config('rag')['database'])
    if not path.is_file():
        raise ValueError('RAG index is required. Run: python vorpal.py knowledge update --import-sources')
    return path


def answer(message, history=(), *, database=None, observations=None, client=None):
    settings = load_config('assistant')
    if not isinstance(message, str) or not message.strip() or len(message) > settings['max_message_chars']:
        raise ValueError(f"Enter a message of 1–{settings['max_message_chars']} characters.")
    path = preflight(database)
    model_settings = dict(load_config('scan_intelligence'))
    # Include bounded conversational context for follow-up questions.
    evidence_terms = []
    if observations:
        for item in observations.get('open_tcp', []):
            evidence_terms.extend(str(item.get(key, '')) for key in ('port', 'service_hint', 'product', 'version'))
    evidence_query = ' '.join(evidence_terms)[:model_settings['max_query_chars'] // 2]
    # The analysis prompt describes output formatting, not the observed service.
    # Including it can both dilute relevant evidence and match unrelated guides.
    query = evidence_query if evidence_query and message == settings['analysis_question'] else evidence_query + ' ' + message
    query += ' ' + ' '.join(turn['user'] for turn in history[-2:])
    docs = rag.search(query[:model_settings['max_query_chars']], path, limit=model_settings['rag_results'])
    if not docs:
        raise ValueError('RAG returned no relevant documents. Update the corpus or ask a more specific question.')
    sources = [{'id': i, 'source': doc['source'], 'line': doc['line'], 'sha256': doc['sha256']}
               for i, doc in enumerate(docs, 1)]
    references = [{'id': i, 'text': doc['text'][:model_settings['max_excerpt_chars']]}
                  for i, doc in enumerate(docs, 1)]
    payload = {'question': message, 'conversation': list(history)[-settings['max_history_turns']:],
               'observations': observations, 'references': references}
    output = (client or scan_intelligence._local_chat)([
        {'role': 'system', 'content': settings['system_prompt']},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=True)}], model_settings)
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise ValueError('The local model returned invalid JSON. Use a local Instruct model.') from exc
    if not isinstance(result, dict):
        raise ValueError('The local model must return a JSON object.')
    text, ids = result.get('answer'), result.get('sources')
    if (not isinstance(text, str) or not text.strip() or len(text) > settings['max_answer_chars']
            or not isinstance(ids, list) or not ids
            or any(type(i) is not int or not 1 <= i <= len(sources) for i in ids)):
        raise ValueError('The local model returned an invalid answer or unsupported source IDs.')
    cited = {int(value) for value in re.findall(r'\[(\d+)\]', text)}
    if cited - set(ids):
        raise ValueError('The answer contains unsupported inline citations.')
    return {'answer': text, 'sources': [s for s in sources if s['id'] in ids],
            'backend': 'local_openai_compatible', 'model': model_settings['model'],
            'rag_required': True, 'automatic_execution': False,
            'caveat': 'Citations identify documents; all model advice requires independent verification.'}


def chat(message, *, session_id=None, report_id=None, database=None, root=ROOT, client=None):
    if not CHAT_LOCK.acquire(blocking=False):
        raise ValueError('Another conversation request is running. Wait and retry.')
    try:
        directory = log_directory(root)
        existing_session = session_id is not None
        session_id = identifier(session_id) if existing_session else secrets.token_hex(12)
        path = directory / f'chat_{session_id}.json'
        if existing_session and not path.is_file():
            raise ValueError('Conversation not found. Start a new conversation.')
        record = read_record(path) if path.exists() else {'session_id': session_id, 'turns': []}
        report_id = identifier(report_id) if report_id else record.get('report_id')
        if record.get('report_id') and record['report_id'] != report_id:
            raise ValueError('Start a new conversation to discuss a different report.')
        report = read_record(directory / f'analysis_{report_id}.json') if report_id else None
        old_database = record.get('database')
        selected_database = str(preflight(database or old_database).resolve())
        if old_database and old_database != selected_database:
            raise ValueError('Start a new conversation to use a different RAG index.')
        result = answer(message, record['turns'], database=selected_database,
                        observations=report.get('observations') if report else None, client=client)
        record['turns'].append({'user': message, 'assistant': result['answer']})
        record['turns'] = record['turns'][-load_config('assistant')['max_history_turns']:]
        record.update(report_id=report_id, database=selected_database,
                      updated_at=datetime.now(timezone.utc).isoformat(), last_response=result)
        save_record(path, record)
        return dict(result, session_id=session_id, report_id=report_id)
    finally:
        CHAT_LOCK.release()


def ports_from_text(raw=None):
    ports = load_config('assistant')['default_ports'] if raw is None else raw
    if isinstance(ports, str):
        try:
            ports = [int(item.strip()) for item in ports.split(',')]
        except ValueError as exc:
            raise ValueError('Ports must be comma-separated integers.') from exc
    if (not isinstance(ports, list) or not ports
            or len(ports) > load_config('assistant')['max_ports']
            or any(type(p) is not int or not 1 <= p <= 65535 for p in ports)):
        raise ValueError('Invalid ports or too many ports selected.')
    return sorted(set(ports))


def analyze_ports(report, report_path, *, database, settings, client=None):
    """Assess each open port independently; keep local citation IDs scoped to that port."""
    observations = report['observations']
    opened = {item['port']: item for item in observations['open_tcp']}
    report['port_analyses'] = []
    report['sources'] = []
    report['status'] = 'analyzing'
    for port in observations['selected_ports']:
        evidence = opened.get(port)
        if evidence is None:
            status = 'closed_or_unreachable' if report['tool'] == 'tcp' else 'not_reported_open'
            report['port_analyses'].append({
                'port': port, 'protocol': 'tcp', 'status': status, 'sources': [],
                'answer': 'Not observed open by this collection. No model assessment was performed; this does not establish safety.'})
        else:
            report['port_analyses'].append({'port': port, 'protocol': 'tcp', 'evidence': evidence,
                                            'status': 'pending', 'sources': []})
    save_record(report_path, report)
    for entry in report['port_analyses']:
        if entry['status'] != 'pending':
            continue
        entry['status'] = 'analyzing'
        save_record(report_path, report)
        context = {'target': observations['target'], 'selected_ports': [entry['port']],
                   'open_tcp': [entry['evidence']]}
        try:
            result = answer(settings['analysis_question'], database=database,
                            observations=context, client=client)
            entry.update(result, status='review_required')
            report['sources'].extend(dict(source, port=entry['port']) for source in result['sources'])
        except (ValueError, OSError, RuntimeError) as exc:
            entry.update(status='failed', error=str(exc),
                         answer='This port could not be assessed. See its error; other port results are retained.')
        save_record(report_path, report)
    completed = sum(entry['status'] == 'review_required' for entry in report['port_analyses'])
    failed = [entry for entry in report['port_analyses'] if entry['status'] == 'failed']
    report['answer'] = (f'{completed} of {len(opened)} observed open TCP ports assessed independently. '
                        'See port_analyses for evidence, advice and port-scoped sources. Findings remain unverified.')
    report.update(backend='local_openai_compatible', model=load_config('scan_intelligence')['model'],
                  rag_required=True, status='partial' if failed else 'review_required' if opened else 'no_open_ports')
    if failed and completed == 0:
        raise ValueError(f"No open port could be assessed: {failed[0]['error']}")


def validate_plan_scope(plan, target, selected):
    if plan['tool'] != 'nmap' or plan['target'] != target or '-p' not in plan['args']:
        raise ValueError('The proposal must use Nmap with the exact target and explicit selected ports.')
    planned = ports_from_text(plan['args'][plan['args'].index('-p') + 1])
    if planned != selected:
        raise ValueError('The proposal changed the selected port scope. Generate a new command.')
    missing = set(load_config('assistant')['nmap_required_flags']) - set(plan['args'])
    if missing:
        raise ValueError('The proposal lacks required evidence capabilities: ' + ', '.join(sorted(missing)))


def analyze_ip(target, *, authorized=False, ports=None, tool=None, goal=None, plan_id=None, digest=None,
               database=None, root=ROOT, client=None):
    if authorized is not True:
        raise ValueError('Explicit authorization is required before connecting to a target.')
    target = security_tools.validate_target('nmap', target)
    selected = ports_from_text(ports)
    settings = load_config('assistant')
    tool = tool if tool is not None else settings['default_tool']
    if tool not in {'tcp', 'nmap'}:
        raise ValueError('Unsupported evidence tool. Select tcp or nmap.')
    path = preflight(database)
    # Verify retrieval before making any target connection.
    if not rag.search(settings['fallback_query'], path, limit=1):
        raise ValueError('RAG has no network security references. Update the corpus before scanning.')
    reviewed_argv = None
    if tool == 'nmap':
        if plan_id is None:
            if digest is not None:
                raise ValueError('A digest requires the reviewed plan ID.')
            goal = goal or settings['planning_goal']
            feedback = None
            rejections = []
            for attempt in range(settings['planning_attempts']):
                plan = None
                try:
                    plan = security_tools.propose('nmap', target, goal, root=root, database=path, client=client,
                                                 constraints={'ports': selected, 'port_selection_owned_by_application': True,
                                                              'required_flags': settings['nmap_required_flags']}, feedback=feedback)
                    validate_plan_scope(plan, target, selected)
                    break
                except (security_tools.ToolPlanError, ValueError) as exc:
                    rejections.append({'attempt': attempt + 1, 'error': str(exc),
                                       'model_response': getattr(exc, 'response', None) or (plan or {}).get('model_response')})
                    if attempt + 1 == settings['planning_attempts']:
                        rejected = security_tools.write_report({'status': 'rejected', 'tool': tool, 'target': target,
                                           'goal': goal, 'selected_ports': selected, 'rejections': rejections}, 'plans', root=root)
                        directory = load_config('security_tools')['runtime']['reports_dir']
                        raise ValueError(f'{exc} Diagnostics: {Path(root) / directory / "plans" / (rejected["id"] + ".json")}') from exc
                    feedback = str(exc)
            plan['planning_attempts'] = attempt + 1
            plan['rejections'] = rejections
            plan = security_tools.write_report(plan, 'plans', root=root)
            execution = [sys.executable, str(Path(root) / 'vorpal.py'), 'analyze', '--target', target,
                         '--tool', 'nmap', '--ports', ','.join(map(str, selected)), '--authorized',
                         '--plan-id', plan['id'], '--digest', plan['digest'], '--rag-db', str(path.resolve())]
            return {'status': 'command_review_required', 'tool': tool, 'target': target, 'selected_ports': selected,
                    'goal': goal, 'command': plan['command'], 'argv': plan['argv'], 'reason': plan['reason'],
                    'plan_id': plan['id'], 'digest': plan['digest'], 'sources': plan['sources'],
                    'planning_attempts': plan['planning_attempts'],
                    'execute_after_review': shlex.join(execution),
                    'automatic_execution': False, 'rag_required': True}
        plan = security_tools.read_plan(plan_id, root=root)
        reviewed_argv = security_tools.reviewed_command(plan, digest, authorized=authorized, root=root)
        validate_plan_scope(plan, target, selected)
        if goal is not None and goal != plan['goal']:
            raise ValueError('The goal changed. Generate and review a new command.')
    elif plan_id is not None or digest is not None or goal is not None:
        raise ValueError('Command planning and review parameters require --tool nmap.')
    report_id = secrets.token_hex(12)
    report_path = log_directory(root) / f'analysis_{report_id}.json'
    observations = {'target': target, 'selected_ports': selected, 'open_tcp': [], 'unreachable_or_closed': []}
    report = {'report_id': report_id, 'tool': tool, 'observations': observations, 'authorized': True,
              'status': 'collecting', 'automatic_execution': False,
              'started_at': datetime.now(timezone.utc).isoformat()}
    save_record(report_path, report)
    try:
        if tool == 'nmap':
            argv = reviewed_argv
            xml_path = report_path.with_suffix('.nmap.xml')
            stderr_path = report_path.with_suffix('.nmap.stderr.log')
            report.update(collector_argv=argv, plan_id=plan_id, plan_digest=digest, goal=plan['goal'],
                          evidence_files={'nmap_xml': str(xml_path), 'stderr': str(stderr_path)})
            save_record(report_path, report)
            collectors.run(argv, xml_path, stderr_path, timeout=settings['nmap_timeout_seconds'],
                           max_bytes=settings['nmap_max_output_bytes'], cwd=root)
            raw = xml_path.read_bytes()
            found = scan_intelligence.parse_nmap(raw, target)
            observations['open_tcp'] = [dict(item, service_hint=item['service'], status='tool_reported_open') for item in found if item['port'] in selected]
            observations.pop('unreachable_or_closed')
        for port in selected if tool == 'tcp' else ():
            try:
                with socket.create_connection((target, port), timeout=settings['connect_timeout_seconds']) as connection:
                    try:
                        banner = connection.recv(settings['banner_bytes']).decode('utf-8', errors='replace')
                    except OSError:
                        banner = ''
                try:
                    hint = socket.getservbyport(port, 'tcp')
                except OSError:
                    hint = 'unknown'
                observations['open_tcp'].append({'port': port, 'service_hint': hint,
                                                 'banner': banner, 'status': 'observed_open'})
            except OSError:
                observations['unreachable_or_closed'].append(port)
        analyze_ports(report, report_path, database=path, settings=settings, client=client)
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    except KeyboardInterrupt:
        report.update(status='cancelled', error='Collection or inference interrupted.')
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save_record(report_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local Ollama + RAG security assistant')
    commands = parser.add_subparsers(dest='action', required=True)
    scan = commands.add_parser('analyze', help='Observe TCP ports on one authorized local IP and analyze with RAG')
    scan.add_argument('--target', required=True)
    scan.add_argument('--authorized', action='store_true')
    scan.add_argument('--ports')
    scan.add_argument('--tool', choices=('tcp', 'nmap'), help='Evidence collector; default from config/assistant.json')
    scan.add_argument('--goal', help='Security objective for the model-generated Nmap proposal')
    scan.add_argument('--plan-id', help='Previously generated command plan to execute after review')
    scan.add_argument('--digest', help='Digest of the reviewed command')
    scan.add_argument('--rag-db')
    scan.add_argument('--output', help='Optional additional JSON report destination')
    conversation = commands.add_parser('chat', help='Security advice with cited local RAG sources')
    conversation.add_argument('--message', help='Omit for an interactive conversation; /exit ends it')
    conversation.add_argument('--session-id')
    conversation.add_argument('--report-id')
    conversation.add_argument('--rag-db')
    args = parser.parse_args(argv)
    try:
        if args.action == 'analyze':
            destination = Path(args.output) if args.output else None
            if destination and destination.is_symlink():
                raise ValueError('Output must not be a symbolic link.')
            result = analyze_ip(args.target, authorized=args.authorized, ports=args.ports, tool=args.tool,
                                goal=args.goal, plan_id=args.plan_id, digest=args.digest, database=args.rag_db)
            if destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
                save_record(destination, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            session = args.session_id
            while True:
                message = args.message if args.message is not None else input('You: ')
                if args.message is None and message.strip() == '/exit':
                    break
                result = chat(message, session_id=session, report_id=args.report_id, database=args.rag_db)
                session = result['session_id']
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if args.message is not None:
                    break
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f'Assistant error: {exc}\n')
    except EOFError:
        print('\nConversation ended.')
    except KeyboardInterrupt:
        print('\nRequest interrupted.')
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
