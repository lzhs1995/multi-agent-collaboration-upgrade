"""Resolve a shared Codex daemon caller from live process evidence, never focus.

No environment mutation or terminal operations. The thread ID is only a selector:
a unique same-user `codex resume <id>` client, executable, birth, TTY and cmux UUID
must agree. Ordinary clients retain the original identify/environment checks.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import cmux_identity_budget as budget


class IdentityError(RuntimeError):
    pass


class ProcessExited(IdentityError):
    """The initial kernel read proved absence, before any identity was read."""


class LoginInfoDenied(IdentityError):
    """Only an initial full-BSD permission refusal, not an absent process."""


class ShortBsdInfo(ctypes.Structure):
    # Public Darwin sys/proc_info.h, PROC_PIDT_SHORTBSDINFO (13).
    _fields_ = [('pid', ctypes.c_uint32), ('ppid', ctypes.c_uint32),
                ('pgid', ctypes.c_uint32), ('status', ctypes.c_uint32),
                ('comm', ctypes.c_char * 16), ('flags', ctypes.c_uint32),
                ('uid', ctypes.c_uint32), ('gid', ctypes.c_uint32),
                ('ruid', ctypes.c_uint32), ('rgid', ctypes.c_uint32),
                ('svuid', ctypes.c_uint32), ('svgid', ctypes.c_uint32),
                ('reserved', ctypes.c_uint32)]


class BsdInfo(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint32), ('status', ctypes.c_uint32),
                ('xstatus', ctypes.c_uint32), ('pid', ctypes.c_uint32),
                ('ppid', ctypes.c_uint32), ('uid', ctypes.c_uint32),
                ('gid', ctypes.c_uint32), ('ruid', ctypes.c_uint32),
                ('rgid', ctypes.c_uint32), ('svuid', ctypes.c_uint32),
                ('svgid', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32),
                ('misc', ctypes.c_uint32 * 6), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


def _args(data, *, native_roots=False):
    argc = ctypes.c_int.from_buffer_copy(data[:4]).value
    if not 1 <= argc <= 65536:
        raise IdentityError('invalid process arguments')
    offset = data.index(b'\0', 4) + 1
    while offset < len(data) and data[offset] == 0:
        offset += 1
    argv = []
    for _ in range(argc):
        end = data.index(b'\0', offset)
        argv.append(data[offset:end].decode())
        offset = end + 1
    env = {}
    for item in data[offset:].split(b'\0'):
        if not item:
            break
        key, sep, value = item.partition(b'=')
        if not sep or key in env:
            raise IdentityError('invalid process environment')
        env[key] = value
    # Credentials never leave this reader.
    selected = ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID', 'CODEX_THREAD_ID')
    if native_roots:
        selected += ('HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR')
    return argv, {k: env[k.encode()].decode() for k in selected if k.encode() in env}


def process(pid, *, arguments=True, validate_argv=True, allow_system_login=False, native_roots=False):
    """Read a same-user process; raw inventory reads are not caller proof.

    Only candidate discovery may defer the argv-path rule. The selected caller
    is reread with the strict default before using its terminal identity.
    """
    budget.check()
    if sys.platform != 'darwin' or type(pid) is not int or pid <= 1:
        raise IdentityError('live Darwin process required')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                ctypes.c_void_p, ctypes.c_int]
    def info(*, initial=False):
        b = BsdInfo()
        ctypes.set_errno(0)
        count = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(b), ctypes.sizeof(b))
        if count != ctypes.sizeof(b):
            if initial and count <= 0 and ctypes.get_errno() == errno.ESRCH:
                raise ProcessExited('process absent at initial kernel read')
            if initial and count <= 0 and allow_system_login and ctypes.get_errno() in (errno.EPERM, errno.EACCES):
                raise LoginInfoDenied('full process identity permission denied')
            raise IdentityError('process identity inaccessible or changed')
        system_login = allow_system_login and b.uid == 0 and os.getuid() != 0
        if b.pid != pid or not b.start_sec or (b.uid != os.getuid() and not system_login):
            raise IdentityError('process identity changed, foreign, or invalid')
        if b.status == 5:
            if initial:
                raise ProcessExited('process zombie at initial kernel read')
            raise IdentityError('process exited during read')
        result = {'pid': pid, 'ppid': b.ppid, 'birth': [b.start_sec, b.start_usec]}
        if system_login:
            result['system_login'] = True
        return result
    result = info(initial=True)
    initial = dict(result)
    if result.get('system_login'):
        # A root-owned macOS login is an ancestry boundary, never a caller.
        # Do not try to read its private argv/env or treat arbitrary foreign
        # processes as evidence that this is an ordinary terminal.
        executable = ctypes.create_string_buffer(4096)
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        def login_path():
            if (lib.proc_pidpath(pid, executable, len(executable)) <= 0
                    or executable.value != b'/usr/bin/login'):
                raise IdentityError('foreign ancestor is not system login')
        login_path()
        st = Path('/usr/bin/login').stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise IdentityError('system login executable is not protected')
        if info() != initial:
            raise IdentityError('system login changed during read')
        login_path()
        budget.check()
        return result
    if arguments:
        size = os.sysconf('SC_ARG_MAX')
        if not 0 < size <= 2 * 1024 * 1024:
            raise IdentityError('unsupported argument buffer')
        buf, length = ctypes.create_string_buffer(size), ctypes.c_size_t(size)
        sysctl = ctypes.CDLL(None).sysctl
        sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        if sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buf, ctypes.byref(length), None, 0) != 0:
            raise IdentityError('process arguments inaccessible')
        argv, env = (_args(buf.raw[:length.value], native_roots=True) if native_roots
                     else _args(buf.raw[:length.value]))
        executable = ctypes.create_string_buffer(4096)
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        if lib.proc_pidpath(pid, executable, len(executable)) <= 0:
            raise IdentityError('executable identity inaccessible')
        path = Path(executable.value.decode()).resolve(strict=True)
        if not argv:
            raise IdentityError('process has no argv')
        if validate_argv and path.name == 'codex' and (not Path(argv[0]).is_absolute()
                                                      or Path(argv[0]).resolve(strict=True) != path):
            raise IdentityError('argv executable differs from kernel executable')
        st = path.stat()
        result.update(argv=argv, env=env, executable=str(path),
                      file_identity=[st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])
    if info() != initial:
        raise IdentityError('process changed during read')
    budget.check()
    return result


def client_candidates():
    """Enumerate kernel executable paths; pgrep -x can omit live Darwin clients.

    ps is discovery only: process() still verifies executable, owner, birth and
    argv before any candidate can become an authenticated caller.
    """
    listing = subprocess.run(['/bin/ps', '-U', str(os.getuid()), '-o', 'pid=,comm='],
                             capture_output=True, text=True, timeout=budget.timeout())
    budget.check()
    if listing.returncode != 0:
        raise IdentityError('client inventory unavailable')
    candidates = []
    for line in listing.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise IdentityError('malformed client inventory')
        if Path(fields[1]).name == 'codex':
            candidates.append(fields[0])
    if len(set(candidates)) != len(candidates):
        raise IdentityError('duplicate client inventory')
    return candidates



def _login_boundary(pid, child):
    """Permission-limited root login, pinned by its still-live original child.

    Short BSD info has no birth time, so it is NOT generic process identity.
    Require the already kernel-read child's complete identity (including birth
    and ppid) before and after. If login exits/reuses its PID, the original child
    is reparented and fails this check. No argv/env from root is read or trusted.
    """
    if (not isinstance(child, dict) or child.get('ppid') != pid
            or not child.get('birth') or not child.get('argv')
            or child.get('system_login')):
        raise IdentityError('system login requires a verified live child')
    if process(child['pid']) != child:
        raise IdentityError('system login child changed before read')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    def short_info():
        budget.check()
        b = ShortBsdInfo()
        if lib.proc_pidinfo(pid, 13, 0, ctypes.byref(b), ctypes.sizeof(b)) != ctypes.sizeof(b):
            raise IdentityError('system login short identity inaccessible')
        if (b.pid != pid or b.uid != 0 or b.ruid != os.getuid()
                or os.getuid() == 0 or b.status == 5 or b.ppid <= 0):
            raise IdentityError('system login short identity foreign or invalid')
        return dict(pid=b.pid, ppid=b.ppid, pgid=b.pgid, uid=b.uid,
                    ruid=b.ruid, svuid=b.svuid)
    def login_path():
        executable = ctypes.create_string_buffer(4096)
        if (lib.proc_pidpath(pid, executable, len(executable)) <= 0
                or executable.value != b'/usr/bin/login'):
            raise IdentityError('foreign ancestor is not system login')
        st = Path('/usr/bin/login').stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise IdentityError('system login executable is not protected')
    first = short_info()
    login_path()
    if short_info() != first:
        raise IdentityError('system login short identity drift')
    login_path()
    if process(child['pid']) != child:
        raise IdentityError('system login child changed or reparented')
    budget.check()
    return dict(pid=pid, ppid=first['ppid'], system_login=True,
                identity_source='short_bsd_stable_child', short_identity=first,
                child_pid=child['pid'], child_birth=list(child['birth']))


def _ancestor_process(pid, child):
    try:
        return process(pid, allow_system_login=True)
    except LoginInfoDenied:
        # No fallback for missing/unknown identity, argv failure, or later drift.
        return _login_boundary(pid, child)


def _ancestry():
    chain, pid, daemon = [], os.getppid(), None
    for _ in range(64):
        budget.check()
        if pid <= 1:
            break
        if any(p['pid'] == pid for p in chain):
            raise IdentityError('cyclic process ancestry')
        item = _ancestor_process(pid, chain[-1] if chain else None)
        chain.append(item)
        if item.get('system_login'):
            break
        argv = item['argv']
        if (Path(item['executable']).name == 'codex' and len(argv) > 1
                and argv[1] == 'app-server' and '--managed-daemon' in argv[2:]):
            daemon = item
            break
        pid = item['ppid']
    else:
        raise IdentityError('process ancestry exceeded limit')
    if daemon is None:
        for index, item in enumerate(chain):
            child = chain[index - 1] if index else None
            if _ancestor_process(item['pid'], child) != item:
                raise IdentityError('ordinary process ancestry drift')
    return chain, daemon


def collect(env):
    """Tool-shell contract: a thread selector requires matching tool ancestry."""
    if sys.platform != 'darwin' or not env.get('CODEX_THREAD_ID'):
        return None
    chain, daemon = _ancestry()
    if daemon is None:
        return None
    session = str(uuid.UUID(env['CODEX_THREAD_ID']))
    tool_threads = [p['env']['CODEX_THREAD_ID'] for p in chain[:-1]
                    if p['env'].get('CODEX_THREAD_ID')]
    if not tool_threads or any(t != session for t in tool_threads):
        raise IdentityError('thread selector differs from tool ancestry')
    if any(env.get(k) != daemon['env'].get(k) for k in
           ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID')):
        raise IdentityError('tool environment differs from managed daemon')
    return _collect_client(session, chain, daemon)


def collect_hook(env, payload):
    """Native hooks carry session_id in stdin, including direct daemon children.

    The payload selects a session only after the actual managed ancestor has
    been read. It never supplies workspace/surface identity or selects by focus.
    A tool-shell thread selector, when present, must agree with the payload.
    """
    if sys.platform != 'darwin':
        return None
    chain, daemon = _ancestry()
    if daemon is None:
        return None
    try:
        session = str(uuid.UUID(payload['session_id']))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IdentityError('managed hook lacks a valid session_id') from exc
    if payload.get('hook_event_name') not in ('PreToolUse', 'PostToolUse', 'Stop', 'SubagentStop',
                                             'SessionStart', 'UserPromptSubmit'):
        raise IdentityError('managed hook event is missing or unsupported')
    selectors = [p['env']['CODEX_THREAD_ID'] for p in chain[:-1]
                 if p['env'].get('CODEX_THREAD_ID')]
    if env.get('CODEX_THREAD_ID'):
        selectors.append(env['CODEX_THREAD_ID'])
    if any(t != session for t in selectors):
        raise IdentityError('hook session differs from thread ancestry')
    if any(env.get(k) != daemon['env'].get(k) for k in
           ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID')):
        raise IdentityError('hook environment differs from managed daemon')
    return _collect_client(session, chain, daemon)


def _collect_client(session, chain, daemon):
    candidates = client_candidates()
    clients = []
    for candidate in candidates:
        try:
            # A discovery row is not yet this session's caller. Read its kernel
            # identity before enforcing the selected caller's argv-path rule.
            p = process(int(candidate), validate_argv=False)
        except ProcessExited:
            # Only an initial ESRCH/zombie is harmless. Unknown identity and
            # a disappearance after a partial read must not hide a second match.
            continue
        argv = p['argv']
        if len(argv) >= 3 and argv[1:3] == ['resume', session]:
            if process(p['pid']) != p:
                raise IdentityError('selected client identity drift')
            if Path(p['executable']).name != 'codex':
                raise IdentityError('client executable mismatch')
            tty = subprocess.run(['/bin/ps', '-p', candidate, '-o', 'tty='],
                                 capture_output=True, text=True,
                                 timeout=budget.timeout(), check=True).stdout.strip()
            budget.check()
            if not tty or tty in ('?', '??', '-'):
                raise IdentityError('client has no terminal')
            clients.append(dict(p, tty=tty))
        elif process(p['pid'], validate_argv=False) != p:
            # A stable, readable nonmatch can be excluded, including a relative
            # argv[0]. Never exclude an unreadable or changing session selector.
            raise IdentityError('unrelated candidate identity drift')
    if len(clients) != 1:
        raise IdentityError('native session has no unique live resumed client')
    # Verify every ancestor as well as the selected client after the inventory.
    for item in chain + clients:
        if process(item['pid']) != {k: v for k, v in item.items() if k != 'tty'}:
            raise IdentityError('process identity drift')
    return {'session': session, 'daemon': daemon, 'client': clients[0], 'chain': chain}


def resolve(identity, tree, env, proof):
    if proof is None:
        return identity, env
    client, daemon = proof['client'], proof['daemon']
    rows = []
    for window in tree.get('windows', []):
        for ws in window.get('workspaces', []):
            for pane in ws.get('panes', []):
                for surface in pane.get('surfaces', []):
                    rows.append(dict(surface_ref=surface.get('ref'), surface_id=surface.get('id'),
                                     workspace_ref=ws.get('ref'), workspace_id=ws.get('id'),
                                     pane_ref=pane.get('ref'), pane_id=pane.get('id'),
                                     window_ref=window.get('ref'), surface_type=surface.get('type'),
                                     dock_scope=surface.get('dock_scope'),
                                     tty=surface.get('tty')))
    def by_env(process_env):
        matches = [r for r in rows if str(r['surface_id']).upper() ==
                   str(process_env.get('CMUX_SURFACE_ID', '')).upper()]
        if len(matches) != 1 or str(matches[0]['workspace_id']).upper() != str(
                process_env.get('CMUX_WORKSPACE_ID', '')).upper():
            raise IdentityError('process UUIDs disagree with live tree')
        if matches[0]['dock_scope'] == 'global':
            raise IdentityError('global dock is not a workspace member')
        return matches[0]
    source, caller = by_env(daemon['env']), by_env(client['env'])
    raw = identity.get('caller') or {}
    if any(raw.get(k) != source[k] for k in ('surface_ref', 'workspace_ref', 'pane_ref')):
        raise IdentityError('identify differs from daemon origin')
    # cmux can retain the name of a recycled PTY on an unrelated surface.
    # The unique live resumed process and its kernel-read UUID environment
    # select the caller; a global TTY-name search must not select or veto it.
    # Still require that exact UUID row to agree with the live client's TTY.
    if (not client.get('tty') or caller['tty'] != client['tty']
            or caller['surface_type'] != 'terminal'):
        raise IdentityError('native client TTY missing or differs from UUID-bound surface')
    resolved_env = dict(env, **{k: client['env'][k] for k in
                               ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID')})
    return dict(identity, caller=caller), resolved_env


def public_proof(proof):
    if proof is None:
        return None
    p = proof['client']
    return dict(session=proof['session'], pid=p['pid'], birth=p['birth'], tty=p['tty'],
                executable=p['executable'], surface_uuid=p['env']['CMUX_SURFACE_ID'],
                daemon_pid=proof['daemon']['pid'],
                argv_sha256=hashlib.sha256(json.dumps(p['argv']).encode()).hexdigest())
