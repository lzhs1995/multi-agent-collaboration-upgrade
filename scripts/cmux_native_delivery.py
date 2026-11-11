"""按原会话新增的完整原生入站记录验真；从不以按键/屏幕推定投递。"""
import hashlib
import functools
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid
from datetime import datetime
import cmux_daemon_identity as identity
from upgrade_claude_registration import matches_process as registration_matches_process

SCHEMA = "native-delivery-v1"
MAX_RECORD = 8 * 1024 * 1024
MAX_SCAN = 64 * 1024 * 1024
IDENTITY_KEYS = ("workspace_uuid", "caller_surface_uuid", "target_surface_uuid", "target_pane_uuid")

class NativeDeliveryError(RuntimeError):
    pass

def _hash(value):
    return hashlib.sha256(value).hexdigest()

def _uuid(value):
    return str(uuid.UUID(value))

def _public_process(p):
    # argv 可含凭据，不写入回执/错误。
    return {k: p[k] for k in ("pid", "birth", "executable", "file_identity")}

def _session_argument(p):
    args, found = p["argv"], []
    for i, a in enumerate(args[1:], 1):
        value = None
        if a in ("resume", "--resume", "-r", "--session-id") and i+1 < len(args):
            value = args[i+1]
        elif a.startswith(("--resume=", "--session-id=")):
            value = a.split("=", 1)[1]
        if value:
            try:
                found.append(_uuid(value))
            except (ValueError, TypeError, AttributeError):
                pass
    if len(set(found)) > 1:
        raise NativeDeliveryError("NATIVE_SESSION_AMBIGUOUS")
    return found[0] if found else None

def _provider(p):
    name = Path(p["executable"]).name
    if name == "codex":
        return "codex"
    if name in ("claude", "claude.exe"):
        return "claude"
    if name == "node" and any("/@anthropic-ai/claude-code/" in arg
            and arg.endswith("/cli.js") for arg in p["argv"][1:3]):
        return "claude"
    return None

def _live_session(p, provider):
    # Claude 原生 /resume 会更新 PID 注册而不改变 argv；只读客户端自己的注册。
    # 不接受本包自行写入的 session 声明，也不把工具进程的祖先当 hook 证明。
    if provider == "claude":
        path = Path.home() / ".claude/sessions" / (str(p["pid"])+".json")
        if os.path.lexists(path):
            with _open(path) as handle:
                raw = handle.read(65537)
                if len(raw) > 65536:
                    raise NativeDeliveryError("NATIVE_REGISTRATION_TOO_LARGE")
                reg = json.loads(raw)
                held, current = os.fstat(handle.fileno()), path.lstat()
                if (held.st_dev, held.st_ino, held.st_size, held.st_mtime_ns) != (
                        current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
                    raise NativeDeliveryError("NATIVE_REGISTRATION_CHANGED")
            if not registration_matches_process(reg, p, observed_at=time.time()):
                raise NativeDeliveryError("NATIVE_REGISTRATION_PROCESS_MISMATCH")
            if identity.process(p["pid"]) != p:
                raise NativeDeliveryError("NATIVE_PROCESS_CHANGED")
            return _uuid(reg["sessionId"])
    selected = _session_argument(p)
    if provider not in ("claude", "codex") or selected is None:
        raise NativeDeliveryError("NATIVE_SESSION_UNRESOLVED: no input; native selector required")
    return selected

def _tree_row(bridge, target):
    tree = json.loads(bridge._run("tree", "--all", "--json", "--id-format", "both"))
    rows = []
    for w in tree.get("windows", []):
        for ws in w.get("workspaces", []):
            for pane in ws.get("panes", []):
                for s in pane.get("surfaces", []):
                    if s.get("id", "").upper() == target.upper():
                        rows.append(dict(workspace_uuid=ws["id"], target_surface_uuid=s["id"],
                            target_pane_uuid=pane["id"], tty=s.get("tty"),
                            kind=s.get("type"), dock=s.get("dock_scope")))
    if len(rows) != 1 or rows[0]["kind"] != "terminal" or rows[0]["dock"] == "global" or not rows[0]["tty"]:
        raise NativeDeliveryError("NATIVE_TARGET_NOT_UNIQUE_TERMINAL")
    return rows[0]

def _target_process(row):
    listing = subprocess.run(["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,comm="],
                             capture_output=True, text=True, timeout=3, check=True)
    matches = []
    for line in listing.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise NativeDeliveryError("NATIVE_PROCESS_INVENTORY_INVALID")
        if Path(fields[1]).name not in ("codex", "claude", "claude.exe", "node"):
            continue
        try:
            p = identity.process(int(fields[0]), validate_argv=False)
        except identity.ProcessExited:
            continue
        if p["env"].get("CMUX_SURFACE_ID", "").upper() != row["target_surface_uuid"].upper():
            continue
        if p["env"].get("CMUX_WORKSPACE_ID", "").upper() != row["workspace_uuid"].upper():
            raise NativeDeliveryError("NATIVE_PROCESS_WORKSPACE_MISMATCH")
        if _provider(p) is None:
            continue
        tty = subprocess.run(["/bin/ps", "-p", str(p["pid"]), "-o", "tty="],
                             capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        if tty in ("?", "??", "-", ""):
            continue
        if tty != row["tty"]:
            raise NativeDeliveryError("NATIVE_PROCESS_TTY_MISMATCH")
        if identity.process(p["pid"]) != p:
            raise NativeDeliveryError("NATIVE_PROCESS_CHANGED")
        matches.append(p)
    if len(matches) != 1:
        raise NativeDeliveryError("NATIVE_TARGET_PROCESS_NOT_UNIQUE")
    return matches[0]

def _native_root(provider):
    if provider not in ("codex", "claude"):
        raise NativeDeliveryError("NATIVE_PROVIDER_UNKNOWN")
    return Path.home() / (".codex/sessions" if provider == "codex" else ".claude/projects")

def _transcript_path(provider, session):
    # 只按精确 session 文件名定位，不按日期/mtime猜测，也不全盘匹配消息。
    pattern = "rollout-*"+session+".jsonl" if provider == "codex" else session+".jsonl"
    found = [p for p in _native_root(provider).rglob(pattern) if p.is_file()]
    if len(found) != 1:
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_NOT_UNIQUE")
    return found[0]

def _open(path):
    p = Path(path)
    if not p.is_absolute() or p.resolve(strict=True) != p:
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_PATH_INVALID")
    f = os.fdopen(os.open(p, os.O_RDONLY | os.O_NOFOLLOW), "rb")
    st = os.fstat(f.fileno())
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        f.close()
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_OWNER_OR_TYPE")
    return f

def _metadata(f, provider, session):
    f.seek(0)
    for _ in range(64):
        raw = f.readline(256*1024+1)
        if not raw or len(raw) > 256*1024 or not raw.endswith(b"\n"):
            break
        try:
            r = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(r, dict):
            continue
        if provider == "codex" and r.get("type") == "session_meta":
            p = r.get("payload", {})
            if not isinstance(p, dict):
                continue
            if p.get("id", p.get("session_id")) != session:
                raise NativeDeliveryError("NATIVE_METADATA_SESSION_MISMATCH")
            return
        if provider == "claude" and r.get("sessionId"):
            if r["sessionId"] != session:
                raise NativeDeliveryError("NATIVE_METADATA_SESSION_MISMATCH")
            return
    raise NativeDeliveryError("NATIVE_SESSION_METADATA_MISSING")

def _pin_file(path, provider, session):
    with _open(path) as f:
        _metadata(f, provider, session)
        st = os.fstat(f.fileno())
        offset = st.st_size
        f.seek(0)
        size = min(offset, 4096)
        head = _hash(f.read(size))
        at = max(0, offset-4096)
        f.seek(at)
        tail = f.read(offset-at)
        now = Path(path).stat()
        if (st.st_dev, st.st_ino) != (now.st_dev, now.st_ino) or now.st_size < offset:
            raise NativeDeliveryError("NATIVE_TRANSCRIPT_CHANGED_DURING_BIND")
        return dict(path=str(path), device=st.st_dev, inode=st.st_ino, offset=offset,
                    prefix_size=size, prefix_sha256=head, tail_at=at,
                    tail_sha256=_hash(tail), at_record_boundary=(not tail or tail.endswith(b"\n")))

def bind_target(bridge, surface, text):
    if not isinstance(text, str) or not text:
        raise NativeDeliveryError("NATIVE_EMPTY_PAYLOAD")
    live = bridge.pin_workspace(surface)
    pins = {k: live[k] for k in IDENTITY_KEYS}
    row = _tree_row(bridge, pins["target_surface_uuid"])
    if any(row[k].upper() != pins[k].upper() for k in IDENTITY_KEYS if k != "caller_surface_uuid"):
        raise NativeDeliveryError("NATIVE_TARGET_MOVED")
    p = _target_process(row)
    provider = _provider(p)
    session = _live_session(p, provider)
    path = _transcript_path(provider, session)
    started = time.time()
    result = dict(schema=SCHEMA, identity=pins, tty=row["tty"], process=_public_process(p),
        provider=provider, session_id=session, transcript=_pin_file(path, provider, session),
        bound_at_epoch=started, payload_sha256=_hash(text.encode()))
    require_bound(bridge, surface, result, text)
    return result

def _check_file(f, binding):
    pin, st = binding["transcript"], os.fstat(f.fileno())
    live = Path(pin["path"]).stat()
    if ((st.st_dev, st.st_ino) != (pin["device"], pin["inode"])
            or (live.st_dev, live.st_ino) != (pin["device"], pin["inode"])
            or st.st_size < pin["offset"]):
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_REPLACED_OR_TRUNCATED")
    f.seek(0)
    if _hash(f.read(pin["prefix_size"])) != pin["prefix_sha256"]:
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_PREFIX_CHANGED")
    f.seek(pin["tail_at"])
    if _hash(f.read(pin["offset"]-pin["tail_at"])) != pin["tail_sha256"]:
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_BASELINE_CHANGED")
    return st.st_size

def _validate_binding(b, text):
    if not isinstance(b, dict) or b.get("schema") != SCHEMA:
        raise NativeDeliveryError("ORIGINAL_NATIVE_BINDING_REQUIRED: no retroactive intent")
    if b.get("payload_sha256") != _hash(text.encode()):
        raise NativeDeliveryError("NATIVE_PAYLOAD_CHANGED")
    if _uuid(b["session_id"]) != b["session_id"]:
        raise NativeDeliveryError("NATIVE_SESSION_INVALID")
    pin = b["transcript"]
    if (not isinstance(pin, dict) or not isinstance(b.get("identity"), dict)
            or set(b["identity"]) != set(IDENTITY_KEYS)
            or any(not isinstance(v, str) or not v for v in b["identity"].values())
            or type(b.get("bound_at_epoch")) not in (int, float)
            or not math.isfinite(b["bound_at_epoch"])):
        raise NativeDeliveryError("NATIVE_BINDING_INVALID")
    if _native_root(b["provider"]).resolve() not in Path(pin["path"]).parents:
        raise NativeDeliveryError("NATIVE_TRANSCRIPT_OUTSIDE_PROVIDER_ROOT")
    if type(pin.get("offset")) is not int or pin["offset"] < 0:
        raise NativeDeliveryError("NATIVE_OFFSET_INVALID")
    if (type(pin.get("at_record_boundary")) is not bool
            or type(pin.get("prefix_size")) is not int
            or pin["prefix_size"] != min(pin["offset"], 4096)
            or type(pin.get("tail_at")) is not int
            or pin["tail_at"] != max(0, pin["offset"]-4096)
            or not Path(pin["path"]).name.endswith(b["session_id"]+".jsonl")):
        raise NativeDeliveryError("NATIVE_BASELINE_INVALID")

def observer_identity(bridge, surface, pins):
    """只读观察允许原发送者或原接收者；反向 pin 仍须通过 live workspace 校验。"""
    try:
        live = bridge.pin_workspace(surface)
    except RuntimeError:
        live = None
    if live is not None and all(live.get(k) == v for k, v in pins.items()):
        return live
    reverse = bridge.pin_workspace(pins["caller_surface_uuid"])
    expected = dict(workspace_uuid=pins["workspace_uuid"],
                    caller_surface_uuid=pins["target_surface_uuid"],
                    target_surface_uuid=pins["caller_surface_uuid"],
                    caller_pane_uuid=pins["target_pane_uuid"])
    if any(reverse.get(k) != v for k, v in expected.items()):
        raise NativeDeliveryError("NATIVE_OBSERVER_IDENTITY_CHANGED")
    return reverse


def require_bound(bridge, surface, b, text, *, read_only=False):
    _validate_binding(b, text)
    if read_only:
        observer_identity(bridge, surface, b["identity"])
    else:
        live = bridge.pin_workspace(surface)
        if any(live.get(k) != v for k, v in b["identity"].items()):
            raise NativeDeliveryError("NATIVE_TARGET_IDENTITY_CHANGED")
    row = _tree_row(bridge, b["identity"]["target_surface_uuid"])
    if row["tty"] != b["tty"] or any(row[k] != b["identity"][k] for k in
            ("workspace_uuid", "target_surface_uuid", "target_pane_uuid")):
        raise NativeDeliveryError("NATIVE_TARGET_TTY_OR_PANE_CHANGED")
    p = identity.process(b["process"]["pid"])
    if _public_process(p) != b["process"]:
        raise NativeDeliveryError("NATIVE_PROCESS_BIRTH_OR_EXECUTABLE_CHANGED")
    tty = subprocess.run(["/bin/ps", "-p", str(p["pid"]), "-o", "tty="],
                         capture_output=True, text=True, timeout=3, check=True).stdout.strip()
    if tty != b["tty"]:
        raise NativeDeliveryError("NATIVE_PROCESS_TTY_CHANGED")
    if any(p["env"].get(k) != b["identity"][v] for k, v in
           (("CMUX_SURFACE_ID", "target_surface_uuid"), ("CMUX_WORKSPACE_ID", "workspace_uuid"))):
        raise NativeDeliveryError("NATIVE_PROCESS_SURFACE_CHANGED")
    if _provider(p) != b["provider"] or _live_session(p, b["provider"]) != b["session_id"]:
        raise NativeDeliveryError("NATIVE_PROCESS_SESSION_CHANGED")
    with _open(b["transcript"]["path"]) as f:
        _check_file(f, b)

def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except ValueError:
        return None

def capture_paste_fence(binding):
    """在原 PASTE_INTENT 落盘前取新鲜 EOF；绝不为旧 attempt 追补。"""
    with _open(binding["transcript"]["path"]) as f:
        _check_file(f, binding)
        captured = time.time()
        pin = _pin_file(binding["transcript"]["path"], binding["provider"], binding["session_id"])
        _check_file(f, binding)
    if any(pin[k] != binding["transcript"][k] for k in ("path", "device", "inode")):
        raise NativeDeliveryError("NATIVE_PASTE_FILE_CHANGED")
    return dict(schema="native-paste-fence-v1", transcript=pin,
                binding_sha256=_hash(json.dumps(binding, sort_keys=True).encode()),
                captured_at_epoch=captured)


def _fenced_binding(binding, text, fence, not_before):
    _validate_binding(binding, text)
    if (not isinstance(fence, dict) or fence.get("schema") != "native-paste-fence-v1"
            or fence.get("binding_sha256") != _hash(json.dumps(binding, sort_keys=True).encode())
            or type(fence.get("captured_at_epoch")) not in (int, float)
            or not math.isfinite(fence["captured_at_epoch"])
            or type(not_before) not in (int, float) or not math.isfinite(not_before)
            or not binding["bound_at_epoch"] <= fence["captured_at_epoch"] <= not_before):
        raise NativeDeliveryError("ORIGINAL_NATIVE_PASTE_FENCE_REQUIRED: no retroactive intent")
    pin = fence.get("transcript")
    if (not isinstance(pin, dict) or any(pin.get(k) != binding["transcript"][k]
            for k in ("path", "device", "inode")) or type(pin.get("offset")) is not int
            or pin["offset"] < binding["transcript"]["offset"]):
        raise NativeDeliveryError("NATIVE_PASTE_FENCE_CHANGED")
    result = dict(binding, transcript=pin)
    _validate_binding(result, text)
    return result


def _inbound(r, b, text, not_before=None, *, fenced=False):
    stamp = _timestamp(r.get("timestamp"))
    earliest = max(b["bound_at_epoch"], not_before or b["bound_at_epoch"])
    # 原生日志可能截断到毫秒。只在新鲜 EOF fence 后允许相同精度区间，
    # bind 到 paste 之间已存在的记录及跨 fence 半行仍完全排除。
    precision = re.search(r"\.(\d{3,6})(?:Z|[+-]\d{2}:\d{2})$", str(r.get("timestamp", "")))
    if fenced and precision:
        scale = 10 ** len(precision[1])
        earliest = math.floor(earliest * scale) / scale
    if stamp is None or stamp < earliest or stamp > time.time()+2:
        return None
    if b["provider"] == "codex":
        p = r.get("payload", {})
        if not isinstance(p, dict):
            return None
        if (r.get("type") == "response_item" and p.get("type") == "message"
                and p.get("role") == "user"
                and p.get("content") == [{"type": "input_text", "text": text}]):
            return "native_user_message"
    else:
        if r.get("sessionId") != b["session_id"] or r.get("isSidechain") is True:
            return None
        if r.get("type") == "user":
            p = r.get("message", {})
            if isinstance(p, dict) and p.get("role") == "user" and p.get("content") in (text, [{"type": "text", "text": text}]):
                return "native_user_message"
        if r.get("type") == "attachment":
            p = r.get("attachment", {})
            if isinstance(p, dict) and p.get("type") == "queued_command" and p.get("prompt") == text:
                return "native_queued_command"
    return None

def probe(b, text, *, cursor=None, max_bytes=MAX_SCAN, not_before=None, paste_fence=None):
    """从 EOF 基线增量读取；预算耗尽返回游标，不跳过中间记录。"""
    original = b
    b = _fenced_binding(b, text, paste_fence, not_before)
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_SCAN:
        raise NativeDeliveryError("NATIVE_SCAN_BUDGET_INVALID")
    pin = b["transcript"]
    start = pin["offset"] if cursor is None else cursor
    if type(start) is not int or start < pin["offset"]:
        raise NativeDeliveryError("NATIVE_CURSOR_INVALID")
    with _open(pin["path"]) as f:
        _check_file(f, original)
        end = _check_file(f, b)
        if start > end:
            raise NativeDeliveryError("NATIVE_CURSOR_TRUNCATED")
        if start > pin["offset"]:
            f.seek(start-1)
            if f.read(1) != b"\n":
                raise NativeDeliveryError("NATIVE_CURSOR_NOT_RECORD_BOUNDARY")
        f.seek(start)
        if start == pin["offset"] and not pin["at_record_boundary"]:
            skipped = f.readline(MAX_RECORD+1)
            if len(skipped) > MAX_RECORD:
                raise NativeDeliveryError("NATIVE_RECORD_LIMIT: preserve attempt")
            if not skipped.endswith(b"\n"):
                return dict(confirmed=False, state="NATIVE_RECORD_INCOMPLETE", next_offset=start)
        scanned, queued = 0, None
        while f.tell() < end and scanned < max_bytes:
            offset = f.tell()
            raw = f.readline(min(MAX_RECORD+1, end-offset))
            if len(raw) > MAX_RECORD:
                raise NativeDeliveryError("NATIVE_RECORD_LIMIT: preserve attempt")
            if not raw.endswith(b"\n"):
                return dict(confirmed=False, state="NATIVE_RECORD_INCOMPLETE", next_offset=offset)
            scanned += len(raw)
            try:
                r = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(r, dict):
                continue
            kind = _inbound(r, b, text, not_before, fenced=True)
            if kind:
                proof = dict(schema=SCHEMA, session_id=b["session_id"], path=pin["path"],
                    device=pin["device"], inode=pin["inode"], offset=offset, length=len(raw),
                    sha256=_hash(raw), reception_kind=kind, payload_sha256=b["payload_sha256"])
                if kind == "native_user_message":
                    _check_file(f, b)
                    return dict(confirmed=True, state="NATIVE_RECEIVED", native_proof=proof,
                                next_offset=offset+len(raw))
                # 排队只是待处理；继续扫描，唯有后续真实 user 记录能确认。
                queued = proof
        cursor = f.tell()
        _check_file(f, b)
        return dict(confirmed=False, state=("NATIVE_SCAN_BUDGET" if cursor < end else
                    "NATIVE_QUEUED" if queued else "NATIVE_PENDING"),
                    queued_proof=queued, next_offset=cursor)

def validate_proof(b, text, proof, *, not_before=None, paste_fence=None):
    try:
        original = b
        b = _fenced_binding(b, text, paste_fence, not_before)
        pin = b["transcript"]
        if (not isinstance(proof, dict) or proof.get("schema") != SCHEMA
                or proof.get("session_id") != b["session_id"]
                or proof.get("payload_sha256") != b["payload_sha256"]
                or any(proof.get(k) != pin[k] for k in ("path", "device", "inode"))
                or type(proof.get("offset")) is not int or proof["offset"] < pin["offset"]
                or type(proof.get("length")) is not int or not 0 < proof["length"] <= MAX_RECORD):
            return False
        with _open(pin["path"]) as f:
            _check_file(f, original)
            _check_file(f, b)
            if proof["offset"] > 0:
                f.seek(proof["offset"]-1)
                if f.read(1) != b"\n":
                    return False
            f.seek(proof["offset"])
            raw = f.read(proof["length"])
            if not raw.endswith(b"\n") or _hash(raw) != proof["sha256"]:
                return False
            r = json.loads(raw)
            return (isinstance(r, dict) and proof.get("reception_kind") == "native_user_message"
                    and _inbound(r, b, text, not_before, fenced=True) == "native_user_message")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, NativeDeliveryError):
        return False

def confirmed(bridge, surface, binding, text, *, proof=None, paste_fence=None, not_before=None):
    require_bound(bridge, surface, binding, text)
    result = probe(binding, text, paste_fence=paste_fence, not_before=not_before) if proof is None else dict(
        confirmed=validate_proof(binding, text, proof, paste_fence=paste_fence, not_before=not_before), native_proof=proof)
    if result.get("confirmed") is not True:
        raise bridge.DispatchUnconfirmed("NATIVE_DELIVERY_UNCONFIRMED: preserve original; no repaste")
    return result


def _original_intent(attempt, binding, text):
    _validate_binding(binding, text)
    if (not isinstance(attempt, dict) or attempt.get("native_binding") != binding
            or not isinstance(attempt.get("binding"), dict)
            or attempt["binding"].get("identity") != binding["identity"]):
        raise NativeDeliveryError("NATIVE_JOURNAL_IDENTITY_MISMATCH")
    events = attempt.get("events")
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        raise NativeDeliveryError("NATIVE_JOURNAL_EVENTS_INVALID")
    pastes = [e for e in events if e.get("phase") == "PASTE_INTENT"]
    if (len(pastes) != 1 or type(pastes[0].get("at_epoch")) not in (int, float)
            or not math.isfinite(pastes[0]["at_epoch"])
            or pastes[0]["at_epoch"] < binding["bound_at_epoch"]
            or not isinstance(pastes[0].get("screen"), str)
            or pastes[0].get("screen_sha256") != _hash(pastes[0]["screen"].encode())[:16]):
        raise NativeDeliveryError("NATIVE_ORIGINAL_PASTE_INTENT_REQUIRED")
    fence = pastes[0].get("native_paste_fence")
    _fenced_binding(binding, text, fence, pastes[0]["at_epoch"])
    return pastes[0]["at_epoch"], fence


def scan_original(bridge, surface, text, attempt_path, binding, *, read_only=False):
    """在原 journal 锁内调用；游标独立落盘，不改写冻结的发送意图。"""
    from cmux_callback_journal import write_json
    _validate_binding(binding, text)
    path = Path(attempt_path)
    attempt = json.loads(path.read_text())
    intent_at, fence = _original_intent(attempt, binding, text)
    require_bound(bridge, surface, binding, text, read_only=read_only)
    # 此文件名不能匹配 attempt-*.json；原报告/发送记录保持不变。
    state_path = path.with_name("native-scan-" + path.stem + ".json")
    binding_hash = _hash(json.dumps(binding, sort_keys=True).encode())
    intent_hash = _hash(json.dumps([intent_at, fence], sort_keys=True).encode())
    old = json.loads(state_path.read_text()) if state_path.exists() else {}
    if old and (old.get("binding_sha256") != binding_hash or old.get("attempt") != str(path)
                or old.get("intent_sha256") != intent_hash):
        raise NativeDeliveryError("NATIVE_SCAN_BINDING_CHANGED")
    if old.get("confirmed") is True:
        if not validate_proof(binding, text, old.get("native_proof"), not_before=intent_at, paste_fence=fence):
            raise NativeDeliveryError("NATIVE_SAVED_PROOF_CHANGED")
        result = old
    else:
        result = probe(binding, text, cursor=old.get("next_offset"), not_before=intent_at, paste_fence=fence)
        if not result.get("queued_proof") and old.get("queued_proof"):
            result["queued_proof"] = old["queued_proof"]
            if result.get("state") == "NATIVE_PENDING":
                result["state"] = "NATIVE_QUEUED"
        write_json(state_path, dict(result, binding_sha256=binding_hash,
                                   intent_sha256=intent_hash, attempt=str(path)))
    require_bound(bridge, surface, binding, text, read_only=read_only)
    return dict(result, native_binding=binding, confirmation_source="native_user_message_v1")


def receipt_evidence(bridge, surface, text, attempt, receipt, *, read_only=False):
    """只核现有回执，屏幕/按键/排队永不升级为 confirmed。"""
    b = attempt.get("native_binding")
    if (receipt.get("confirmed") is not True or not b or receipt.get("native_binding") != b
            or receipt.get("confirmation_source") != "native_user_message_v1"):
        return False
    intent_at, fence = _original_intent(attempt, b, text)
    require_bound(bridge, surface, b, text, read_only=read_only)
    return validate_proof(b, text, receipt.get("native_proof"), not_before=intent_at, paste_fence=fence)

def register_hook(payload):
    """已退役：普通工具与 hook 共享祖先，手工 payload 不能绑定原生会话。"""
    raise NativeDeliveryError("NATIVE_MANUAL_REGISTRATION_DISABLED: zero side effects")


def _bounded_error(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except NativeDeliveryError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError,
                subprocess.SubprocessError, identity.IdentityError) as exc:
            # 不输出 argv/env；原故障保留在异常链中，调用者只收到有界失败。
            raise NativeDeliveryError("NATIVE_OBSERVATION_UNAVAILABLE: " + type(exc).__name__ +
                "; preserve original attempt; inspect the native client session") from exc
    return wrapped


bind_target = _bounded_error(bind_target)
require_bound = _bounded_error(require_bound)
observer_identity = _bounded_error(observer_identity)
scan_original = _bounded_error(scan_original)
register_hook = _bounded_error(register_hook)
