"""Extract report candidates from MCP index-list envelopes; no network or config writes."""
from __future__ import annotations

from merchant_setup import validate_id


def candidates(envelopes: list[dict]) -> dict:
    reports: dict[str, dict] = {}
    pages = []
    usable = 0
    for number, envelope in enumerate(envelopes, 1):
        response = envelope.get('response', envelope) if isinstance(envelope, dict) else {}
        if not isinstance(response, dict):
            response = {}
        data = response.get('data')
        rows = data.get('rows') if isinstance(data, dict) else None
        valid = (response.get('tool_id') == 'get_custom_report_index_page_list'
                 and response.get('status') in ('complete', 'partial')
                 and not response.get('error_code') and isinstance(rows, list))
        page = {'input': number, 'usable': valid, 'status': response.get('status'),
                'trace_id': response.get('trace_id'), 'coverage': response.get('coverage'),
                'pagination': response.get('pagination'), 'warnings': response.get('warnings', []),
                'output_validation': response.get('output_validation'),
                'error_code': response.get('error_code'), 'invalid_entries': 0}
        pages.append(page)
        if not valid:
            continue
        usable += 1
        for row in rows:
            links = row.get('reportList') if isinstance(row, dict) else None
            if links is None:
                continue
            if not isinstance(links, list):
                page['invalid_entries'] += 1
                continue
            for link in links:
                if not isinstance(link, dict):
                    page['invalid_entries'] += 1
                    continue
                value = link.get('reportId')
                if isinstance(value, int) and not isinstance(value, bool):
                    value = str(value)
                try:
                    report_id = validate_id(value)
                except ValueError:
                    page['invalid_entries'] += 1
                    continue
                item = reports.setdefault(report_id, {'report_id': report_id, 'names': [], 'source_pages': []})
                name = link.get('reportName')
                if isinstance(name, str) and name.strip() and name not in item['names']:
                    item['names'].append(name)
                if number not in item['source_pages']:
                    item['source_pages'].append(number)
    return {'status': 'candidates' if reports else ('empty' if usable else 'unavailable'),
            'source': 'index_report_associations', 'report_catalog_complete': False,
            'candidates': list(reports.values()), 'pages': pages,
            'note': '关联候选不是完整报表目录；使用前核对用途、字段和权限，不自动选择第一条。'}
