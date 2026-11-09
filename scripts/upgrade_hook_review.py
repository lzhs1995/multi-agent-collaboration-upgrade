"""Review a finite private hook inventory via the official configuration API.

This optional preparation runs one offline metadata server, before config is
frozen. It never creates a thread, invokes a hook, or admits a model task.
"""
from pathlib import Path
import json
import os
import selectors
import shlex
import subprocess
import time

from orchestration_common import (file_ref, json_ref, read_ref, require, verify_tree,
                                  write_bytes_once, write_json)
from upgrade_task_hook_preflight import validate_inventory
from upgrade_live_integrity import verify_artifacts
import upgrade_toml as tomllib

POLICY = 'official-reviewed-hooks-before-freeze-v1'


def review_commands(hook_ref, source, python_ref):
    """Only commands in the caller's frozen generated controller are eligible."""
    verify_tree(source)
    hooks = json_ref(hook_ref)['hooks']
    scripts = Path(source['path']) / 'scripts'
    reviewed = []
    for event, groups in hooks.items():
        for group in groups:
            for item in group['hooks']:
                words = shlex.split(item['command'])
                require(item['type'] == 'command' and len(words) >= 4
                        and words[:3] == [python_ref['path'], '-I', '-B'],
                        'HOOK_REVIEW_ONLY_FROZEN_PYTHON_COMMANDS')
                path = Path(words[3])
                require(path.parent == scripts and 'scripts/' + path.name in source['files'],
                        'HOOK_REVIEW_SCRIPT_OUTSIDE_CONTROLLER')
                if path.name == 'cmux_workflow_advisory.py':
                    require(len(words) == 6 and words[4] == '--hook'
                            and Path(words[5]).name == words[5]
                            and 'scripts/' + words[5] in source['files'],
                            'HOOK_REVIEW_ADVISORY_TARGET_CHANGED')
                elif path.name == 'mac_upgrade_child_hook.py':
                    require(words[4:] == ['hook'], 'HOOK_REVIEW_CHILD_ARGUMENTS_CHANGED')
                else:
                    require(len(words) == 4, 'HOOK_REVIEW_UNEXPECTED_ARGUMENTS')
                reviewed.append({'event': event, 'matcher': group.get('matcher'), 'handler': item})
    require(0 < len(reviewed) <= 256, 'HOOK_REVIEW_INVENTORY_REQUIRED')
    return reviewed


class OfflineServer:
    """One private stdio connection. Timeouts are terminal and never replayed."""
    def __init__(self, binary, native_root, work, directory):
        self.directory, self.sequence, self.buffer = Path(directory), 0, b''
        self.started = time.monotonic()
        env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'TMPDIR')
               if k in os.environ}
        env['CODEX_HOME'] = str(native_root)
        self.argv = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)',
                     binary['path'], 'app-server', '--listen', 'stdio://']
        write_json(self.directory / 'launch-intent.json', dict(argv=self.argv, binary=binary,
            native_root=str(native_root), work_root=str(work), model_turns=0), exclusive=True)
        self.stderr = (self.directory / 'stderr.log').open('xb')
        self.process = subprocess.Popen(self.argv, cwd=work, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def request(self, method, params):
        require(method in ('initialize', 'config/read', 'hooks/list', 'config/batchWrite'),
                'HOOK_REVIEW_METHOD_FORBIDDEN')
        self.sequence += 1
        message = dict(id=self.sequence, method=method, params=params)
        write_json(self.directory / f'{self.sequence:03d}-intent.json', message, exclusive=True)
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        deadline = min(self.started + 55, time.monotonic() + 10)
        while time.monotonic() < deadline:
            if b'\n' not in self.buffer:
                require(bool(self.selector.select(max(0, deadline - time.monotonic()))),
                        'HOOK_REVIEW_RPC_TIMEOUT_NO_REPLAY')
                chunk = os.read(self.process.stdout.fileno(), 65536)
                require(bool(chunk), 'HOOK_REVIEW_SERVER_EOF')
                self.buffer += chunk
                require(len(self.buffer) <= 4 * 1024 * 1024, 'HOOK_REVIEW_RESPONSE_TOO_LARGE')
                continue
            raw, self.buffer = self.buffer.split(b'\n', 1)
            response = json.loads(raw)
            with (self.directory / 'wire.jsonl').open('ab') as stream:
                stream.write(raw + b'\n')
            if response.get('id') == self.sequence:
                write_json(self.directory / f'{self.sequence:03d}-response.json', response, exclusive=True)
                require('result' in response and 'error' not in response, 'HOOK_REVIEW_RPC_ERROR_NO_REPLAY')
                return response['result']
        require(False, 'HOOK_REVIEW_RPC_TIMEOUT_NO_REPLAY')

    def close(self):
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=10)
            write_json(self.directory / 'exit.json', dict(returncode=code, forced_stop=False), exclusive=True)
            require(code == 0, 'HOOK_REVIEW_SERVER_EXIT_FAILED')
        except subprocess.TimeoutExpired:
            write_json(self.directory / 'exit-unknown.json', dict(pid=self.process.pid,
                forced_stop=False, replay_allowed=False), exclusive=True)
            raise
        finally:
            self.selector.close(); self.process.stdout.close(); self.stderr.close()


def apply_review(client, spec, config_path, reviewed):
    """Each write selects exactly one verified current server key/hash."""
    configured = json_ref(spec['pins']['codex/hooks.json'])['hooks']
    require(reviewed == [{'event': event, 'matcher': group.get('matcher'), 'handler': item}
                        for event, groups in configured.items() for group in groups
                        for item in group['hooks']], 'HOOK_REVIEW_EXACT_REVIEW_REQUIRED')
    first = client.request('hooks/list', {'cwds': [spec['work_root']]})
    initial = validate_inventory(spec, first)
    require(len(reviewed) == initial['handler_count'] == len(initial['pending']),
            'HOOK_REVIEW_FRESH_UNTRUSTED_INVENTORY_REQUIRED')
    items = first['data'][0]['hooks']
    require(all(x['trustStatus'] == 'untrusted' for x in items), 'HOOK_REVIEW_FRESH_STATE_REQUIRED')
    cfg = client.request('config/read', {'includeLayers': True, 'cwd': spec['work_root']})
    layers = [x for x in cfg['layers'] if x['name']['type'] == 'user'
              and x['name']['file'] == str(config_path)]
    require(len(layers) == 1, 'HOOK_REVIEW_CONFIG_LAYER_CHANGED')
    version = layers[0]['version']
    trusted = {}
    for item in items:
        read_ref(spec['pins']['codex/hooks.json'])
        update = {item['key']: {'trusted_hash': item['currentHash']}}
        result = client.request('config/batchWrite', {'edits': [{'keyPath': 'hooks.state',
            'value': update, 'mergeStrategy': 'upsert'}], 'filePath': str(config_path),
            'expectedVersion': version, 'reloadUserConfig': True})
        require(result.get('status') == 'ok' and result.get('filePath') == str(config_path)
                and result.get('overriddenMetadata') is None and isinstance(result.get('version'), str),
                'HOOK_REVIEW_WRITE_NOT_CONFIRMED')
        version = result['version']
        trusted.update(update)
    after = client.request('hooks/list', {'cwds': [spec['work_root']]})
    checked = validate_inventory(spec, after)
    require(checked['ready'] and {x['key']: x['currentHash'] for x in after['data'][0]['hooks']}
            == {x['key']: x['currentHash'] for x in items}, 'HOOK_REVIEW_READBACK_CHANGED')
    return trusted, checked


def prepare(binary_ref, config_seed, hook_ref, *, source, python_ref, work_root, directory):
    """Explicit new preparation only; caller freezes the returned final config."""
    verify_artifacts([binary_ref]); seed_raw = read_ref(config_seed); read_ref(python_ref)
    config_path, hooks_path = Path(config_seed['path']), Path(hook_ref['path'])
    require(config_path.name == 'config.toml' and hooks_path == config_path.parent / 'hooks.json',
            'HOOK_REVIEW_PRIVATE_HOME_REQUIRED')
    seed = tomllib.loads(seed_raw.decode())
    require('hooks' not in seed, 'HOOK_REVIEW_SEED_ALREADY_HAS_TRUST')
    reviewed = review_commands(hook_ref, source, python_ref)
    directory = Path(directory); directory.mkdir(mode=0o700, exist_ok=False)
    archived_seed = write_bytes_once(directory / 'config-before.toml', seed_raw)
    write_json(directory / 'review.json', dict(configuration=hook_ref, source=source,
        reviewed=reviewed, binary=binary_ref, config_seed=archived_seed,
        native_config_path=str(config_path)), exclusive=True)
    client = OfflineServer(binary_ref, config_path.parent, work_root, directory)
    try:
        initialized = client.request('initialize', {'clientInfo': {'name': 'upgrade_hook_review',
            'version': '1', 'title': 'Reviewed private hook configuration'},
            'capabilities': {'experimentalApi': True}})
        require(initialized.get('codexHome') == str(config_path.parent), 'HOOK_REVIEW_HOME_CHANGED')
        client.process.stdin.write(b'{"method":"initialized","params":{}}\n')
        spec = {'pins': {'codex/hooks.json': hook_ref}, 'work_root': str(work_root)}
        states, inventory = apply_review(client, spec, config_path, reviewed)
    except Exception as exc:
        write_json(directory / 'failure.json', dict(error=str(exc), error_type=type(exc).__name__,
            replay_allowed=False, model_turns=0), exclusive=True)
        raise
    finally:
        client.close()
    verify_artifacts([binary_ref]); read_ref(hook_ref); verify_tree(source)
    final = tomllib.loads(config_path.read_text())
    require(final == dict(seed, hooks={'state': states}), 'HOOK_REVIEW_UNRELATED_CONFIG_CHANGED')
    require(not list((config_path.parent / 'sessions').glob('**/*.jsonl')),
            'HOOK_REVIEW_UNEXPECTED_SESSION_CREATED')
    return dict(schema=POLICY, configuration=hook_ref, config_seed=archived_seed,
        config_final=file_ref(config_path), reviewed=reviewed, inventory=inventory,
        states=states, metadata_processes=1, thread_starts=0, model_turns=0,
        automatic_execution_proven=False, evidence_root=str(directory))
