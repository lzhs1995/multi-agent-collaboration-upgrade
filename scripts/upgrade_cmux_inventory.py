"""One bounded terminal metadata snapshot; no native reader or sender imports."""
import subprocess

from orchestration_common import require


class ReadOnlyCmux:
    def __init__(self, executable):
        self.executable = executable
        self.snapshot = None

    def _run(self, *args):
        require(args == ('tree', '--all', '--json', '--id-format', 'both'),
                'INSPECTOR_METADATA_ONLY')
        if self.snapshot is None:
            self.snapshot = subprocess.run([self.executable, *args], capture_output=True,
                                           text=True, timeout=10, check=True).stdout
        return self.snapshot
