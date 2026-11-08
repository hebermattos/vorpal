"""Ollama-compatible security chat and explicitly authorized TCP observations."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import socket
import sys
import threading

try:
    from .config import load_config
    from . import rag, scan_intelligence, security_tools
except ImportError:
    from config import load_config
    import rag, scan_intelligence, security_tools

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
    return json.loads(scan_intelligence._read(path, load_config('assistant')['max_session_bytes']))


def save_record(path, value):
    if path.is_symlink():
        raise ValueError('Assistant log must not be a symbolic link.')
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    if len(payload.encode('utf-8')) > load_config('assistant')['max_session_bytes']:
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
    query = message
    if observations:
        query += ' ' + ' '.join(str(item.get('service_hint', '')) for item in observations.get('open_tcp', []))
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
    ports = load_config('local_vuln_scan')['policy']['default_ports'] if raw is None else raw
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


def analyze_ip(target, *, authorized=False, ports=None, database=None, root=ROOT, client=None):
    if authorized is not True:
        raise ValueError('Explicit authorization is required before connecting to a target.')
    target = security_tools.validate_target('nmap', target)
    selected = ports_from_text(ports)
    settings = load_config('assistant')
    path = preflight(database)
    # Verify retrieval before making any target connection.
    if not rag.search(settings['fallback_query'], path, limit=1):
        raise ValueError('RAG has no network security references. Update the corpus before scanning.')
    report_id = secrets.token_hex(12)
    report_path = log_directory(root) / f'analysis_{report_id}.json'
    observations = {'target': target, 'selected_ports': selected, 'open_tcp': [], 'unreachable_or_closed': []}
    report = {'report_id': report_id, 'observations': observations, 'authorized': True,
              'status': 'collecting', 'automatic_execution': False,
              'started_at': datetime.now(timezone.utc).isoformat()}
    save_record(report_path, report)
    try:
        for port in selected:
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
        report['status'] = 'analyzing'
        save_record(report_path, report)
        question = settings['analysis_question']
        result = answer(question, database=path, observations=observations, client=client)
        report.update(result, status='review_required')
    except Exception as exc:
        report.update(status='failed', error=str(exc))
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
            result = analyze_ip(args.target, authorized=args.authorized, ports=args.ports, database=args.rag_db)
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
    except (EOFError, KeyboardInterrupt):
        print('\nConversation ended.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
