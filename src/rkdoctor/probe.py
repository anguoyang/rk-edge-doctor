"""Read-only probes. Missing, inaccessible or malformed sources return None."""

import json
import fnmatch
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any, List, Optional


class Probe:
    def __init__(self, root: str = "/") -> None:
        self.root = Path(os.path.abspath(root))

    @property
    def live(self) -> bool:
        return self.root == Path("/")

    def path(self, source: str) -> Optional[Path]:
        """Redirect paths; do not follow snapshot symlinks outside its root."""
        try:
            root = self.root.resolve()
            target = (root / source.lstrip("/")).resolve()
            target.relative_to(root)
            return target
        except (OSError, ValueError, RuntimeError):
            return None

    def read_bytes(self, source: str, limit: int = 1048576) -> Optional[bytes]:
        path = self.path(source)
        if path is None:
            return None
        try:
            with path.open("rb") as stream:
                return stream.read(limit)
        except (OSError, ValueError):
            return None

    def read(self, source: str) -> Optional[str]:
        content = self.read_bytes(source)
        if content is None:
            return None
        return content.decode("utf-8", errors="replace").strip("\x00 \t\r\n")

    def exists(self, source: str) -> Optional[bool]:
        path = self.path(source)
        if path is None:
            return None
        try:
            path.stat()
            return True
        except (FileNotFoundError, NotADirectoryError):
            return False
        except (OSError, ValueError):
            return None

    def glob(self, pattern: str) -> Optional[List[str]]:
        # pathlib.glob silently suppresses some permission errors. Traverse
        # explicitly so inaccessible directories remain distinct from absence.
        try:
            sources = [""]
            for part in pattern.strip("/").split("/"):
                if not any(char in part for char in "*?["):
                    sources = [source + "/" + part for source in sources]
                    continue
                expanded = []
                for source in sources:
                    directory = self.path(source)
                    if directory is None:
                        return None
                    try:
                        with os.scandir(directory) as entries:
                            expanded.extend(source + "/" + entry.name for entry in entries
                                            if fnmatch.fnmatchcase(entry.name, part))
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                sources = expanded
            found = []
            for source in sources:
                present = self.exists(source)
                if present is None:
                    return None
                if present:
                    found.append(source)
            return sorted(found)
        except (OSError, ValueError, RuntimeError):
            return None

    def command(self, args: List[str]) -> Optional[str]:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=5, check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            return None

    def system(self, field: str) -> Optional[str]:
        try:
            value = getattr(platform, field)()
            return value or None
        except (OSError, ValueError, AttributeError):
            return None

    def disk_usage(self) -> Any:
        try:
            return shutil.disk_usage(self.root)
        except (OSError, ValueError):
            return None

    def python_module(self, name: str) -> dict:
        # Isolate native extension crashes, noisy imports and slow initialization.
        script = (
            "import importlib,json\n"
            "try:\n"
            " m=importlib.import_module(" + repr(name) + ")\n"
            " v=getattr(m,'__version__',None)\n"
            " print('RKDOCTOR:'+json.dumps({'present':True,"
            "'version':str(v) if v else 'unknown'}))\n"
            "except ModuleNotFoundError as e:\n"
            " print('RKDOCTOR:'+json.dumps({'present':False if e.name=="
            + repr(name) + " else None,'version':'unknown',"
            "'reason':'module absent' if e.name==" + repr(name)
            + " else 'dependency import failed'}))\n"
            "except BaseException:\n"
            " print('RKDOCTOR:'+json.dumps({'present':None,"
            "'version':'unknown','reason':'import failed'}))\n"
        )
        output = self.command([sys.executable, "-B", "-c", script])
        for line in reversed((output or "").splitlines()):
            if line.startswith("RKDOCTOR:"):
                try:
                    result = json.loads(line[len("RKDOCTOR:"):])
                    if isinstance(result, dict) and "present" in result:
                        return result
                except (ValueError, TypeError):
                    break
        return {"present": None, "version": "unknown",
                "reason": "import failed, timed out or process unavailable"}
