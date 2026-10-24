#!/usr/bin/env python3
"""Execute public-source verifications without a checkout credential or secrets."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request


def validate_inputs(env):
    repository = env['INPUT_REPOSITORY']
    sha = env['INPUT_SHA']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('repository must be owner/name')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('sha must be an exact lowercase 40-character commit')
    runner = env['INPUT_RUNNER']
    linux = r'avrea-ubuntu-(?:latest|22\.04|24\.04|26\.04)-(?:arm-)?(?:(?:1|2|4|8|16|32)-vcpu|4-vcpu-8gb|8-vcpu-16gb)'
    if not re.fullmatch(linux + r'|avrea-macos-26-(?:8|16)-vcpu|avrea-windows-2025-(?:2|4|8|16)-vcpu|ubuntu-latest|windows-latest|macos-14', runner):
        raise ValueError('unsupported runner label (GitHub labels are timing controls only)')
    if '-arm-32-' in runner or '22.04-arm-' in runner:
        raise ValueError('unsupported ARM size/image')
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', env['INPUT_REQUEST_ID']):
        raise ValueError('invalid request ID')
    commands = json.loads(env.get('INPUT_MATRIX', '[]'))
    if not isinstance(commands, list):
        raise ValueError('matrix must be a JSON array')
    commands = commands or [env['INPUT_COMMAND']]
    if not 1 <= len(commands) <= 32 or any(not isinstance(c, str) or not c.strip() for c in commands):
        raise ValueError('matrix must contain 1..32 nonempty command strings')
    parallel = int(env['INPUT_PARALLEL'])
    timeout = int(env['INPUT_TIMEOUT'])
    if not 1 <= parallel <= 32 or not 1 <= timeout <= 360:
        raise ValueError('parallel must be 1..32 and timeout 1..360 minutes')
    for glob in env.get('INPUT_FETCH', '').splitlines():
        parts = Path(glob).parts
        if not glob or glob.startswith(('/', '~', '!', '\\')) or '..' in parts or '.git' in parts or ':' in glob or '\\' in glob:
            raise ValueError('fetch globs must be relative public output paths, not .git')
    return [{'index': i, 'command': c} for i, c in enumerate(commands)], parallel, timeout


def select_index(root, requested):
    if requested:
        if requested not in {'official', 'avrea'}:
            raise ValueError('index must be official or avrea')
        return requested
    for name in ('uv.lock', 'poetry.lock'):
        path = root / name
        if path.is_file() and 'pypi.org' in path.read_text():
            return 'official'
    for path in root.rglob('*requirements*.txt'):
        if '.git' not in path.parts:
            text = path.read_text()
            if '--hash=' in text:
                return 'official'
    return 'avrea'


def reset_signals():
    for name in ('SIGHUP', 'SIGINT', 'SIGQUIT'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)


def validate():
    matrix, parallel, timeout = validate_inputs(os.environ)
    request = urllib.request.Request('https://api.github.com/repos/' + os.environ['INPUT_REPOSITORY'], headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'fleet-runner'})
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    if metadata.get('private') is not False or metadata.get('visibility') != 'public':
        raise ValueError('only public repositories are allowed')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('matrix=' + json.dumps(matrix, separators=(',', ':')) + '\n')
        output.write(f'parallel={parallel}\ntimeout={timeout + 5}\n')


def execute():
    env = os.environ
    actual = subprocess.check_output(['git', '-C', 'target', 'rev-parse', 'HEAD'], text=True).strip()
    if actual != env['INPUT_SHA']:
        raise ValueError('checkout SHA does not match exact requested head')
    result_dir = Path(env['RUNNER_TEMP']) / ('fleet-result-' + env['INPUT_INDEX'])
    result_dir.mkdir(parents=True, exist_ok=True)
    # Commands receive no GitHub/Avrea API credentials. Target checkout never persists one.
    child_env = {k: v for k, v in env.items() if k not in {'GITHUB_TOKEN', 'GH_TOKEN', 'AVR_TOKEN'} and not k.startswith('INPUT_')}
    index = select_index(Path('target'), env.get('INPUT_PACKAGE_INDEX', ''))
    if index == 'official':
        for key in ('PIP_EXTRA_INDEX_URL', 'UV_EXTRA_INDEX_URL', 'UV_INDEX', 'UV_DEFAULT_INDEX'):
            child_env.pop(key, None)
        child_env['PIP_INDEX_URL'] = 'https://pypi.org/simple'
        child_env['UV_INDEX_URL'] = 'https://pypi.org/simple'
    command = env['INPUT_COMMAND']
    started = time.time()
    with open(result_dir / 'stdout.log', 'wb') as log:
        process = subprocess.Popen(['bash', '-o', 'pipefail', '-c', command], cwd='target', env=child_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt', preexec_fn=reset_signals if os.name != 'nt' else None)
        try:
            code = process.wait(timeout=int(env['INPUT_TIMEOUT']) * 60)
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], check=False)
            process.wait()
            code = 124
    code = code if code >= 0 else 128 - code
    receipt = {'repository': env['INPUT_REPOSITORY'], 'sha': actual, 'runner': env['INPUT_RUNNER'], 'index': int(env['INPUT_INDEX']), 'command': command, 'exit_code': code, 'started_at': dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(), 'duration_seconds': time.time() - started, 'run_id': env['GITHUB_RUN_ID'], 'run_attempt': env['GITHUB_RUN_ATTEMPT']}
    receipt['package_index'] = index
    (result_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print((result_dir / 'stdout.log').read_text(errors='replace'))
    fetch = '\n'.join(str(Path(env['GITHUB_WORKSPACE']) / 'target' / p) for p in env.get('INPUT_FETCH', '').splitlines())
    delimiter = 'FLEET_' + os.urandom(12).hex()
    with open(env['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'fetch<<{delimiter}\n{fetch}\n{delimiter}\n')
    return min(code, 255)


if __name__ == '__main__':
    try:
        sys.exit(execute() if sys.argv[1] == 'execute' else validate())
    except (ValueError, KeyError, urllib.error.URLError) as error:
        print(f'fleet-runner: {error}', file=sys.stderr)
        sys.exit(2)
