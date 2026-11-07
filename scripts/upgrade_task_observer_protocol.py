"""Wire-only read protocol; no service, actor or execution capability."""
import os
from pathlib import Path
import re
import stat
import time

from orchestration_common import require, strict_json

SCHEMA = "upgrade-task-observer-endpoint-v1"
REQUEST = "upgrade-task-observer-request-v1"
RESPONSE = "upgrade-task-observer-response-v1"
OBSERVATION = "upgrade-task-observation-v1"
KINDS = ("tree", "task")
PROCESS_FIELDS = ("pid", "ppid", "birth", "executable", "file_identity")
MAX_REPLY = 256 * 1024


def socket_pin(path):
    path = Path(path)
    st = path.lstat()
    require(path.is_absolute() and path.parent == path.parent.resolve()
            and stat.S_ISSOCK(st.st_mode) and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o600,
            "TASK_OBSERVER_SOCKET_CHANGED")
    return dict(path=str(path), device=st.st_dev, inode=st.st_ino,
                uid=st.st_uid, mode=stat.S_IMODE(st.st_mode))


def request_value(value):
    require(isinstance(value, dict) and set(value) == {"schema", "kind", "nonce"}
            and value["schema"] == REQUEST and value["kind"] in KINDS
            and isinstance(value["nonce"], str) and re.fullmatch(r"[a-f0-9]{48}", value["nonce"]),
            "TASK_OBSERVER_REQUEST_REFUSED")
    return value


def frame(connection, *, limit, seconds):
    data = bytearray()
    deadline = time.monotonic() + seconds
    while b'\n' not in data:
        require(time.monotonic() < deadline, "TASK_OBSERVER_FRAME_TIMEOUT")
        connection.settimeout(max(.001, deadline - time.monotonic()))
        chunk = connection.recv(min(4096, limit + 1 - len(data)))
        require(chunk, "TASK_OBSERVER_FRAME_INCOMPLETE")
        data.extend(chunk)
        require(len(data) <= limit, "TASK_OBSERVER_FRAME_TOO_LARGE")
    require(data.endswith(b'\n') and data.count(b'\n') == 1, "TASK_OBSERVER_FRAME_CHANGED")
    return strict_json(bytes(data), 'original observer frame')
