"""Conservative changed-line Python review. Source is parsed, never executed."""
import ast
import hashlib
import re
import time
from .diff import parse_diff, _path
from .model import ai_review

RULES = {
 'mutable-default': ('medium', 'Mutable default argument persists across calls', 'Lists, dictionaries, and sets used as defaults are shared by all calls.', 'Use None as the default and create a fresh collection inside the function.', 'Call the function twice and verify the second call starts with fresh state.'),
 'bare-except': ('medium', 'Bare exception handler hides interrupts', 'A bare except catches SystemExit and KeyboardInterrupt as well as ordinary errors.', 'Catch the specific exception types you expect.', 'Verify unexpected exceptions and KeyboardInterrupt propagate.'),
 'dynamic-execution': ('high', 'Dynamic execution requires trusted input', 'eval/exec executes Python code; externally controlled input can execute arbitrary operations.', 'Replace dynamic execution with a parser or an explicit allowlisted operation.', 'Pass hostile input and verify it is rejected without executing code.'),
 'shell-true': ('high', 'Shell execution expands command text', 'shell=True allows shell metacharacters in command text to execute additional commands if input is untrusted.', 'Pass an argument list with shell=False and validate external inputs.', 'Pass an argument containing shell metacharacters and verify it stays a literal argument.'),
 'unsafe-yaml-load': ('high', 'YAML load lacks an explicit safe loader', 'yaml.load without SafeLoader can deserialize unsafe Python objects depending on the library version.', 'Use yaml.safe_load or yaml.load with yaml.SafeLoader.', 'Verify YAML object tags cannot construct Python objects.'),
}


def _finding(path, line, rule):
    severity, title, explanation, suggestion, test = RULES[rule]
    return dict(path=path, line=line, rule=rule, severity=severity, title=title, explanation=explanation, suggestion=suggestion, test=test, source='static')


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + '.' + node.attr
    return ''


def _checks(source):
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = (node.module or '') + '.' + alias.name
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for value in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                if isinstance(value, (ast.List, ast.Dict, ast.Set)) or (isinstance(value, ast.Call) and _name(value.func) in ('list', 'dict', 'set') and not value.args and not value.keywords):
                    yield value.lineno, 'mutable-default'
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            yield node.lineno, 'bare-except'
        elif isinstance(node, ast.Call):
            name = _name(node.func)
            first, _, rest = name.partition('.')
            name = aliases.get(first, first) + ('.' + rest if rest else '')
            if name in ('eval', 'exec', 'builtins.eval', 'builtins.exec'):
                yield node.lineno, 'dynamic-execution'
            if name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output'):
                for kw in node.keywords:
                    if kw.arg == 'shell' and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        yield kw.value.lineno, 'shell-true'
            if name == 'yaml.load':
                loader = next((kw.value for kw in node.keywords if kw.arg == 'Loader'), node.args[1] if len(node.args) > 1 else None)
                loader_name = _name(loader)
                first, _, rest = loader_name.partition('.')
                loader_name = aliases.get(first, first) + ('.' + rest if rest else '')
                if loader_name not in ('yaml.SafeLoader', 'yaml.CSafeLoader'):
                    yield node.lineno, 'unsafe-yaml-load'


def review(diff, files=None, use_ai=False):
    started = time.perf_counter()
    parsed = parse_diff(diff)
    if files is not None and (not isinstance(files, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in files.items())):
        raise ValueError('files must map paths to full source text')
    rows = diff.splitlines()
    new_paths = {_path(rows[i + 1][4:]) for i, row in enumerate(rows[:-1]) if row == '--- /dev/null' and rows[i + 1].startswith('+++ ')}
    warnings, findings = [], []
    for file in parsed:
        path = file['path']
        if not path.endswith('.py'):
            continue
        source = files.get(path) if files else None
        if source is None:
            lines = file['lines']
            if path in new_paths and lines and all(l['kind'] == 'add' for l in lines) and file['added_lines'] == list(range(1, len(lines) + 1)):
                source = '\n'.join(l['text'] for l in lines) + '\n'
        if source is not None:
            if len(source.encode()) > 500_000:
                warnings.append(f'{path}: source exceeds 500 KB; static analysis skipped.')
                continue
            source_lines = source.splitlines()
            if any(l['new_line'] > len(source_lines) or source_lines[l['new_line'] - 1] != l['text'] for l in file['lines'] if l['kind'] != 'delete'):
                warnings.append(f'{path}: source does not match diff citations; static analysis skipped.')
                continue
            try:
                checks = list(_checks(source))
            except (SyntaxError, ValueError, RecursionError):
                warnings.append(f'{path}: complete source could not be parsed; AST checks skipped.')
                checks = []
        else:
            warnings.append(f'{path}: partial diff; complete source is required for static checks.')
            checks = []
        for line, rule in checks:
            if line in file['added_lines']:
                findings.append(_finding(path, line, rule))
    mode, model = 'static', None
    if use_ai:
        try:
            extra, model = ai_review(parsed)
            findings.extend(extra)
            mode = 'ai'
        except Exception as exc:
            mode = 'static_fallback'
            warnings.append(f'Local AI unavailable or returned invalid output ({type(exc).__name__}); showing static results.')
    unique = {}
    for finding in findings:
        key = (finding['path'], finding['line'], finding['rule'])
        finding['id'] = hashlib.sha256(repr(key).encode()).hexdigest()[:12]
        unique[key] = finding
    return dict(findings=list(unique.values()), mode=mode, warnings=warnings, files=parsed, duration_ms=round((time.perf_counter()-started)*1000, 2), model=model)
