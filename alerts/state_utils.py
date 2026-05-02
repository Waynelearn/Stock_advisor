"""Safe state file read/write — atomic writes prevent corruption.

All modules should use safe_load_state() and safe_save_state() instead of
raw json.load/dump to prevent empty/corrupted state files from crashing
the alert system.
"""

import json
import os
import tempfile


def safe_load_state(path: str, default: dict | None = None) -> dict:
    """Load JSON state file with corruption recovery.

    If file is missing, empty, or corrupted, returns default (or {}).
    """
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default.copy()
    try:
        with open(path) as f:
            content = f.read().strip()
            if not content:
                print(f"[STATE] Empty file, resetting: {os.path.basename(path)}")
                return default.copy()
            return json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[STATE] Corrupted file, resetting: {os.path.basename(path)} ({e})")
        return default.copy()
    except Exception as e:
        print(f"[STATE] Read error: {os.path.basename(path)} ({e})")
        return default.copy()


def safe_save_state(path: str, state: dict):
    """Write JSON state file atomically — write to temp file, then rename.

    This prevents empty/corrupted files from power loss or disk-full events.
    """
    try:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"[STATE] Write error: {os.path.basename(path)} ({e})")
