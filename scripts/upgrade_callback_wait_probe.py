"""Offline contract probe for the actual materialized callback journal."""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import types
from unittest.mock import patch


def run(journal, root, target_root):
    root, target_root = Path(root), Path(target_root)
    root.mkdir(parents=True, exist_ok=True)
    target_root.mkdir(parents=True, exist_ok=True)
    results = []
    real_sleep = time.sleep

    class Refusal(RuntimeError):
        pass

    def fixture(name):
        folder = root / name
        folder.mkdir()
        report = folder / 'REPORT.md'
        report.write_text('frozen result')
        pack_path = folder / 'task-pack.json'
        receipt = folder / 'receipt.json'
        pack = dict(task_id=name, completion_nonce='nonce-' + name,
                    report=str(report), callback_target='surface:offline',
                    completion_receipt=str(receipt), executor_uuid='EXECUTOR',
                    completion_callback='DONE|' + name)
        pack_path.write_text(json.dumps(pack))
        proof = dict(workspace_uuid='WS', caller_surface_uuid='EXECUTOR',
                     target_surface_uuid='SUPERVISOR', target_pane_uuid='PANE')
        calls = []

        def send(*args, **kwargs):
            calls.append('simulated_submit')
            # Stop at the unchanged transport boundary; no native reception claim.
            raise Refusal('OFFLINE_TRANSPORT_BOUNDARY')

        bridge = types.SimpleNamespace(TaskPackContractError=Refusal, DispatchUnconfirmed=Refusal,
            validate_task_pack_contract=lambda p: json.loads(Path(p).read_text()),
            pin_workspace=lambda target: copy.deepcopy(proof), submit_text=send,
            _sha256_file=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest())
        pane = target_root / ('pane-' + hashlib.sha256(b'WS:PANE').hexdigest() + '.lock')
        target = target_root / ('target-' + hashlib.sha256(b'SUPERVISOR').hexdigest() + '.lock')
        return types.SimpleNamespace(folder=folder, report=report, path=pack_path, pack=pack,
            proof=proof, calls=calls, bridge=bridge, pane=pane, target=target,
            receipt=receipt, attempts=receipt.with_name(receipt.stem + '-attempts'))

    def invoke(f, expected, **kwargs):
        try:
            journal.deliver(f.bridge, f.path, **kwargs)
        except Refusal as exc:
            assert expected in str(exc), (expected, str(exc))
        else:
            raise AssertionError('expected refusal at ' + expected)

    def held(path):
        handle = journal.open_regular_lock(path)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle

    def untouched(f):
        assert not f.calls and not list(f.attempts.glob('attempt-*.json')) and not f.receipt.exists()

    with patch('availability_contract.require_action') as action, \
            patch.object(journal.native, 'bind_target', return_value={}) as bind:
        f = fixture('release')
        lock = held(f.pane)
        before = f.pane.stat()
        thread = threading.Thread(target=lambda: (real_sleep(0.15), lock.close()))
        thread.start()
        try:
            invoke(f, 'OFFLINE_TRANSPORT_BOUNDARY')
        finally:
            thread.join(2)
        assert f.calls == ['simulated_submit'] and len(list(f.attempts.glob('attempt-*.json'))) == 1
        assert f.pane.stat().st_ino == before.st_ino and action.call_count == 2
        assert bind.call_count == 1
        results.append('same_inode_release_one_original_invocation')

        f = fixture('timeout')
        with held(f.pane), patch.object(journal, 'CALLBACK_PANE_WAIT_SECONDS', 0.15):
            start = time.monotonic()
            invoke(f, 'PANE_TIMEOUT_BEFORE_INPUT')
            assert 0.14 <= time.monotonic() - start < 2
        untouched(f)
        results.append('persistent_contention_deadline_zero_input')

        for mode in ('replace_pane', 'replace_task', 'pack', 'report', 'identity', 'expiry', 'receipt', 'attempt'):
            f = fixture(mode)
            lock = held(f.pane)
            ran = []

            def mutate(seconds):
                if ran:
                    raise AssertionError('unexpected second wait')
                ran.append(True)
                if mode.startswith('replace_'):
                    path = f.pane if mode == 'replace_pane' else f.attempts / 'delivery.lock'
                    path.rename(path.with_suffix('.old'))
                    path.touch()
                elif mode == 'pack':
                    value = dict(f.pack, task_id='changed')
                    f.path.write_text(json.dumps(value))
                elif mode == 'report':
                    f.report.write_text('changed report')
                elif mode == 'identity':
                    f.proof['target_pane_uuid'] = 'MOVED'
                elif mode == 'expiry':
                    action.side_effect = Refusal('AVAILABILITY_EXPIRED')
                elif mode == 'receipt':
                    f.receipt.write_text('{}')
                else:
                    (f.attempts / 'attempt-0001.json').write_text('{}')
                lock.close()

            expected = ('LOCK_CHANGED' if mode.startswith('replace_') else
                        'INPUT_CHANGED' if mode in ('pack', 'report') else
                        'IDENTITY_CHANGED' if mode == 'identity' else
                        'AVAILABILITY_EXPIRED' if mode == 'expiry' else 'JOURNAL_CHANGED')
            try:
                with patch.object(journal.time, 'sleep', side_effect=mutate):
                    invoke(f, expected)
            finally:
                lock.close()
                action.side_effect = None
            assert ran and not f.calls
            if mode not in ('receipt', 'attempt'):
                untouched(f)
            results.append(mode + '_during_wait_zero_input')

        for mode in ('reconcile_only', 'resume_queue_only', 'old_attempt'):
            f = fixture(mode)
            kwargs = {} if mode == 'old_attempt' else {mode: True}
            if mode == 'old_attempt':
                f.attempts.mkdir()
                (f.attempts / 'attempt-0001.json').write_text(json.dumps({'phase': 'NO_INPUT'}))
            with held(f.pane), patch.object(journal.time, 'sleep') as sleep:
                invoke(f, 'CALLBACK_PANE_IN_PROGRESS', **kwargs)
                sleep.assert_not_called()
            assert not f.calls
            results.append(mode + '_retains_fail_fast')

        f = fixture('target')
        with held(f.target), patch.object(journal.time, 'sleep') as sleep:
            invoke(f, 'CALLBACK_TARGET_IN_PROGRESS')
            sleep.assert_not_called()
        untouched(f)
        results.append('target_lock_retains_fail_fast')

        f = fixture('task')
        f.attempts.mkdir()
        with held(f.attempts / 'delivery.lock'), patch.object(journal.time, 'sleep') as sleep:
            invoke(f, 'CALLBACK_IN_PROGRESS')
            sleep.assert_not_called()
        untouched(f)
        results.append('task_lock_retains_fail_fast')

        f = fixture('native_gate')
        with patch.object(journal.native, 'bind_target', side_effect=Refusal('STOP_OR_ADOPTION_REFUSED')):
            invoke(f, 'STOP_OR_ADOPTION_REFUSED')
        assert not f.calls
        assert len(list(f.attempts.glob('attempt-*.json'))) == 1
        results.append('original_native_gate_still_refuses')

    return dict(checks=results, simulated_transport_calls=1, model_requests=0, native_inputs=0)


def pressure(journal, root, target_root, workers=8, rounds=10):
    """Real processes sharing one persistent pane inode; transport stays simulated."""
    root = Path(root)
    root.mkdir(parents=True)
    release = root / 'release'
    audit = root / 'transport.jsonl'
    pids = []

    def record(event, name):
        fd = os.open(audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(dict(event=event, name=name, pid=os.getpid())) + '\n').encode())
        finally:
            os.close(fd)

    for worker in range(workers):
        pid = os.fork()
        if pid:
            pids.append(pid)
            continue
        try:
            (root / ('ready-' + str(worker))).touch()
            start = time.monotonic()
            while not release.exists():
                if time.monotonic() - start > 10:
                    raise RuntimeError('pressure barrier timeout')
                time.sleep(0.01)
            class Boundary(RuntimeError):
                pass
            with patch('availability_contract.require_action'), \
                    patch.object(journal.native, 'bind_target', return_value={}):
                for iteration in range(rounds):
                    name = str(worker) + '-' + str(iteration)
                    folder = root / name
                    folder.mkdir()
                    report = folder / 'REPORT.md'
                    report.write_text('immutable')
                    pack = dict(task_id=name, completion_nonce=name, report=str(report),
                                callback_target='surface:offline', executor_uuid='EXECUTOR',
                                completion_receipt=str(folder / 'receipt.json'), completion_callback='DONE|' + name)
                    path = folder / 'task-pack.json'
                    path.write_text(json.dumps(pack))
                    calls = []
                    def send(*args, **kwargs):
                        calls.append(1)
                        record('enter', name)
                        time.sleep(0.01)
                        record('leave', name)
                        raise Boundary('OFFLINE_BOUNDARY')
                    bridge = types.SimpleNamespace(TaskPackContractError=RuntimeError,
                        DispatchUnconfirmed=RuntimeError,
                        validate_task_pack_contract=lambda p: json.loads(Path(p).read_text()),
                        pin_workspace=lambda target: dict(workspace_uuid='WS', caller_surface_uuid='EXECUTOR',
                            target_surface_uuid='SUPERVISOR', target_pane_uuid='PANE'),
                        submit_text=send, _sha256_file=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest())
                    try:
                        journal.deliver(bridge, path)
                    except Boundary:
                        pass
                    assert calls == [1]
                    assert len(list((folder / 'receipt-attempts').glob('attempt-*.json'))) == 1
                    assert not (folder / 'receipt.json').exists()
            os._exit(0)
        except BaseException as exc:
            (root / ('failure-' + str(worker))).write_text(repr(exc))
            os._exit(1)
    start = time.monotonic()
    while len(list(root.glob('ready-*'))) < workers:
        if time.monotonic() - start > 10:
            raise RuntimeError('parent barrier timeout')
        time.sleep(0.01)
    release.touch()
    statuses = [os.waitpid(pid, 0)[1] for pid in pids]
    assert statuses == [0] * workers, {p.name: p.read_text() for p in root.glob('failure-*')}
    active, completed = set(), set()
    for row in map(json.loads, audit.read_text().splitlines()):
        if row['event'] == 'enter':
            assert not active and row['name'] not in completed
            active.add(row['name'])
        else:
            assert active == {row['name']}
            active.remove(row['name'])
            completed.add(row['name'])
    assert not active and len(completed) == workers * rounds
    return dict(workers=workers, rounds=rounds, original_invocations=len(completed),
                duplicate_attempts=0, simultaneous_transport_overlap=0, model_requests=0, native_inputs=0)
