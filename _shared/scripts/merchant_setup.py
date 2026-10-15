"""Persistent report setup shared by all nine skills; no network or credentials."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

KINDS = ('daily_ops', 'inventory', 'profit')


def config_dir() -> Path:
    override = os.environ.get('SELLFOX_USER_CONFIG_DIR')
    return Path(override).expanduser().resolve() if override else Path.home() / '.sellfox-skills' / 'config'


def read_state() -> dict:
    path = config_dir() / 'reports.json'
    if not path.exists():
        return {'schema_version': 1, 'reports': {}}
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or obj.get('schema_version') != 1 or not isinstance(obj.get('reports'), dict):
        raise ValueError('用户报表配置格式错误；请修复或恢复备份，不要覆盖原文件。')
    for kind, item in obj['reports'].items():
        if kind not in KINDS or not isinstance(item, dict) or item.get('status') not in ('verified', 'deferred'):
            raise ValueError('用户报表配置条目无效。')
        if item['status'] == 'verified':
            validate_id(item.get('id'))
    return obj


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('报表 ID 格式无效；助手应从实时查询结果取得有效 ID，不把名称、链接或凭证作为 ID。')
    return value


def overlay_reports(base: dict) -> dict:
    # A deferred choice explicitly clears only that report; bundled defaults stay available.
    result = dict(base)
    reports = {k: dict(v) for k, v in (base.get('reports') or {}).items()}
    for kind, item in read_state()['reports'].items():
        reports.setdefault(kind, {})['id'] = item['id'] if item['status'] == 'verified' else ''
    result['reports'] = reports
    return result


def write_state(obj: dict) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'reports.json'
    old = target.read_bytes() if target.exists() else None
    # Keep the last usable state. Atomic replacement avoids partially written JSON.
    if old is not None:
        backup = directory / 'reports.json.bak'
        backup.write_bytes(old)
        backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(prefix='.reports-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_deferred(kind: str) -> bool:
    return read_state()['reports'].get(kind, {}).get('status') == 'deferred'

def _update(kind: str, report_id: str | None = None, evidence: dict | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError('未知报表用途。')
    obj = read_state()
    if report_id is None:
        entry = {'status': 'deferred'}
    else:
        report_id = validate_id(report_id)
        proof = evidence or {}
        if not isinstance(proof, dict):
            raise ValueError('验证证据必须是对象。')
        request, response = proof.get('request', {}), proof.get('response', {})
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise ValueError('验证证据格式错误。')
        params = request.get('params')
        scope = response.get('scope') or {}
        if not isinstance(params, dict) or not isinstance(scope, dict):
            raise ValueError('验证参数或响应范围格式错误。')
        tool = 'get_custom_report_page_list'
        if (request.get('tool_id') != tool or response.get('tool_id') != tool
                or params.get('report_id') != report_id
                or response.get('status') != 'complete' or response.get('error_code')
                or not response.get('trace_id')
                or not response.get('contract_version')
                or request.get('contract_version') != response.get('contract_version')):
            raise ValueError('报表尚未通过同一 ID 的只读验证；原配置保持不变。')
        data = response.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('rows'), list):
            raise ValueError('报表返回缺少 rows 列表，不能保存为已验证。')
        scope_id = scope.get('report_id')
        if scope_id is not None and str(scope_id) != report_id:
            raise ValueError('响应报表 ID 不匹配，原配置保持不变。')
        entry = {'id': report_id, 'status': 'verified', 'trace_id': response['trace_id'],
                 'validated_at': datetime.now(timezone.utc).isoformat(),
                 'validation': 'readable_only', 'empty': not bool(data['rows'])}
    obj['reports'][kind] = entry
    write_state(obj)
    return entry


def update(kind: str, report_id: str | None = None, evidence: dict | None = None) -> dict:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / '.reports.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('另一个配置操作正在进行；请稍后重试。异常退出遗留的锁须确认无写入后再清理。') from None
    try:
        os.close(fd)
        return _update(kind, report_id, evidence)
    finally:
        lock.unlink()


def status() -> dict:
    from config_loader import ConfigLoader
    state = read_state()['reports']
    effective = ConfigLoader().load('reports').get('reports', {})
    return {'config_file': str(config_dir() / 'reports.json'), 'reports': {
        kind: {'status': state.get(kind, {}).get('status', 'legacy' if effective.get(kind, {}).get('id') else 'unset'),
               'id': effective.get(kind, {}).get('id', ''),
               'validation': state.get(kind, {}).get('validation'),
               'empty': state.get(kind, {}).get('empty'),
               'prompt': False,
               'auto_discover': kind not in state and not bool(effective.get(kind, {}).get('id'))}
        for kind in KINDS}}


def main() -> None:
    parser = argparse.ArgumentParser(description='赛狐报表引导助手（不联网，不保存 MCP Key）')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    discovery = sub.add_parser('candidates', help='从指标列表完整信封提取关联报表候选，不保存配置')
    discovery.add_argument('--input', type=Path, nargs='+', required=True)
    defer = sub.add_parser('defer')
    defer.add_argument('--kind', choices=KINDS, required=True)
    save = sub.add_parser('save')
    save.add_argument('--kind', choices=KINDS, required=True)
    save.add_argument('--id', required=True)
    save.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'candidates':
            from report_discovery import candidates
            result = candidates([json.loads(path.read_text(encoding='utf-8')) for path in args.input])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.command == 'defer':
            update(args.kind)
        elif args.command == 'save':
            update(args.kind, args.id, json.loads(args.evidence.read_text(encoding='utf-8')))
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'配置未完成：{exc}\n')


if __name__ == '__main__':
    main()
