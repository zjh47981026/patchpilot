"""Read-only public GitHub import. No credentials, redirects, clones or code execution."""
import base64
import json
import re
import time
import ssl
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler, HTTPSHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('GitHub redirects are refused; use the canonical repository URL.')

_PATTERN = re.compile(r'^https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)(?:/)?$')
# python.org macOS builds may not have a configured OpenSSL CA bundle.
# Load the OS-maintained root bundle while retaining certificate verification.
_TLS = ssl.create_default_context()
if sys.platform == 'darwin' and Path('/etc/ssl/cert.pem').is_file():
    _TLS.load_verify_locations('/etc/ssl/cert.pem')
_OPENER = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=_TLS))

def _get(path, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Import exceeded its 45-second budget.')
    req = Request('https://api.github.com' + path, headers={
        'Accept':'application/vnd.github+json', 'User-Agent':'PatchPilot/1.0',
        'X-GitHub-Api-Version':'2026-03-10'})
    try:
        with _OPENER.open(req, timeout=min(8, remaining)) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError('GitHub response exceeds the import budget.')
        return json.loads(body)
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise ValueError('GitHub rate limit reached. Try later or paste a diff.') from None
        if exc.code == 404:
            raise ValueError('Public PR not found. Private repositories are not supported.') from None
        raise ValueError('GitHub could not serve this PR.') from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise ValueError('GitHub import failed or timed out. Try again or paste a diff.') from None

def import_pr(url):
    if not isinstance(url, str) or not (match := _PATTERN.fullmatch(url.strip())):
        raise ValueError('Use a public PR URL: https://github.com/owner/repository/pull/123')
    owner, repo, number = match.groups()
    if repo in ('.', '..'):
        raise ValueError('Invalid repository name.')
    root = f'/repos/{owner}/{repo}/pulls/{number}'
    deadline = time.monotonic() + 45
    meta = _get(root, deadline)
    if not isinstance(meta, dict) or meta.get('changed_files', 0) > 30:
        raise ValueError('This PR exceeds the 30-file budget. Paste a smaller diff.')
    head, base = meta['head']['sha'], meta['base']['sha']
    if not all(re.fullmatch(r'[a-f0-9]{40}', sha) for sha in (head, base)):
        raise ValueError('Invalid commit provenance.')
    head_repo = (meta['head'].get('repo') or {}).get('full_name', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', head_repo):
        raise ValueError('The PR head repository is unavailable.')
    entries = _get(root + '/files?per_page=100', deadline)
    if not isinstance(entries, list) or len(entries) != meta['changed_files']:
        raise ValueError('GitHub returned incomplete file coverage.')
    chunks, sources, warnings = [], {}, []
    total = 0
    for entry in entries:
        path = entry['filename']
        if not path.endswith('.py'):
            warnings.append(f'{path}: unsupported file type, omitted from Python review.')
            continue
        # Paths are only metadata and API URL segments, never local filenames.
        if any(part in ('', '.', '..') for part in path.split('/')) or '\\' in path or any(ord(c)<32 for c in path):
            raise ValueError('Unsafe GitHub file path.')
        patch = entry.get('patch')
        if not patch:
            warnings.append(f'{path}: GitHub supplied no textual patch; file omitted.')
            continue
        old_path = entry.get('previous_filename', path)
        if any(part in ('', '.', '..') for part in old_path.split('/')) or '\\' in old_path or any(ord(c)<32 for c in old_path):
            raise ValueError('Unsafe GitHub previous path.')
        removed = entry.get('status') == 'removed'
        new_file = entry.get('status') == 'added'
        chunks.append(f'diff --git a/{old_path} b/{path}\n--- ' + ('/dev/null' if new_file else 'a/'+old_path) + '\n+++ ' + ('/dev/null' if removed else 'b/'+path) + '\n' + patch + '\n')
        if not removed:
            content = _get(f'/repos/{head_repo}/contents/{quote(path, safe="/")}?ref={head}', deadline)
            if not isinstance(content, dict) or content.get('type') != 'file' or content.get('encoding') != 'base64' or content.get('size', 500_001) > 500_000:
                warnings.append(f'{path}: full source unavailable within budget; only diff context reviewed.')
                continue
            try:
                raw = base64.b64decode(content['content'], validate=False)
                total += len(raw)
                if total > 1_000_000:
                    raise ValueError('Total imported source exceeds 1 MB.')
                sources[path] = raw.decode('utf-8')
            except UnicodeDecodeError:
                warnings.append(f'{path}: source is not UTF-8; full-source analysis unavailable.')
    current = _get(root, deadline)
    if current['head']['sha'] != head or current['base']['sha'] != base:
        raise ValueError('PR changed during import. Retry for a consistent snapshot.')
    diff = ''.join(chunks)
    if not diff:
        raise ValueError('No reviewable Python patches were found in this PR.')
    if len(diff.encode()) > 250_000:
        raise ValueError('PR patch exceeds 250 KB. Paste a smaller diff.')
    return dict(title=str(meta.get('title', 'GitHub pull request'))[:200], diff=diff, files=sources,
                warnings=warnings, origin=dict(url=url.strip(), head_sha=head, base_sha=base,
                repository=f'{owner}/{repo}', number=int(number), coverage='Python textual patches only'))
