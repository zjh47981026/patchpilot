"""Bounded unified diff parser. Never reads paths from disk."""
import re
from pathlib import PurePosixPath

MAX_BYTES = 250_000
MAX_FILES = 30
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$')


def _path(raw):
    raw = raw.split('\t', 1)[0]
    if raw == '/dev/null':
        return None
    if raw.startswith(('a/', 'b/')):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if not raw or '\\' in raw or raw.startswith('/') or any(p in ('..', '.') for p in raw.split('/')) or '\x00' in raw or ':' in raw:
        raise ValueError('Unsafe or unsupported diff path')
    return str(path)


def parse_diff(diff):
    if not isinstance(diff, str) or len(diff.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Diff must be text of at most 250 KB')
    rows = diff.splitlines()
    result = []
    current = None
    i = 0
    while i < len(rows):
        row = rows[i]
        if row.startswith('--- '):
            old_path = _path(row[4:])
            if i + 1 >= len(rows) or not rows[i + 1].startswith('+++ '):
                raise ValueError('Missing new file header')
            new_path = _path(rows[i + 1][4:])
            if old_path is None and new_path is None:
                raise ValueError('Invalid file headers')
            current = {'path': new_path or old_path, 'lines': [], 'added_lines': []}
            if any(f['path'] == current['path'] for f in result):
                raise ValueError('Duplicate file path')
            result.append(current)
            if len(result) > MAX_FILES:
                raise ValueError('Diff contains more than 30 files')
            i += 2
            continue
        if row.startswith('@@'):
            if current is None:
                raise ValueError('Hunk without file header')
            match = HUNK.fullmatch(row)
            if not match:
                raise ValueError('Malformed hunk header')
            old, old_count, new, new_count = match.groups()
            old, new = int(old), int(new)
            old_count, new_count = int(old_count or 1), int(new_count or 1)
            if (old_count and old == 0) or (new_count and new == 0):
                raise ValueError('Invalid hunk line start')
            previous_old = [l['old_line'] for l in current['lines'] if l['old_line'] is not None]
            previous_new = [l['new_line'] for l in current['lines'] if l['new_line'] is not None]
            if (previous_old and old_count and old <= previous_old[-1]) or (previous_new and new_count and new <= previous_new[-1]):
                raise ValueError('Overlapping or unordered hunks')
            used_old = used_new = 0
            i += 1
            while used_old < old_count or used_new < new_count:
                if i >= len(rows):
                    raise ValueError('Truncated hunk')
                value = rows[i]
                if value == '\\ No newline at end of file':
                    i += 1
                    continue
                if not value or value[0] not in ' +-':
                    raise ValueError('Malformed hunk body')
                prefix = value[0]
                kind = {'+': 'add', '-': 'delete', ' ': 'context'}[prefix]
                item = {'kind': kind, 'old_line': old + used_old if prefix != '+' else None, 'new_line': new + used_new if prefix != '-' else None, 'text': value[1:]}
                used_old += prefix != '+'
                used_new += prefix != '-'
                if used_old > old_count or used_new > new_count:
                    raise ValueError('Hunk count mismatch')
                current['lines'].append(item)
                if prefix == '+':
                    current['added_lines'].append(item['new_line'])
                i += 1
            continue
        if row.startswith(('+++ ', '+', '-',' ')) and row:
            raise ValueError('Unexpected diff content outside hunk')
        if row and not row.startswith(('diff --git ', 'index ', 'new file mode ', 'deleted file mode ', 'old mode ', 'new mode ', 'similarity index ', 'rename from ', 'rename to ', '\\ No newline at end of file')):
            raise ValueError('Unsupported diff content')
        i += 1
    if not result or not any(f['lines'] for f in result):
        raise ValueError('No supported text hunks found')
    return result
