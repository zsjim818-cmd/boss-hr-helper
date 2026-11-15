"""Small, optional local cache for versioned scoring outputs.

The engine supplies the full versioned key and decides which states are eligible
for caching. This module performs no scoring, status filtering, or network I/O.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Optional, Union


def stable_hash(value: Any) -> str:
    """Hash a JSON value with stable mapping order and strict finite numbers.

    Unsupported values raise TypeError/ValueError instead of silently changing
    identity through a fallback string representation.
    """
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ScoringCache:
    """Memory cache with optional atomic JSON persistence.

    Keys may be strings or other JSON values. Disk filenames contain only their
    SHA256 digest. ``issues`` records safe read/write misses without candidate
    payloads; an unavailable disk leaves the in-memory cache usable.
    """

    FORMAT_VERSION = 1

    def __init__(self, directory: Optional[Union[str, Path]] = None):
        self.issues = []
        self._values = {}
        self._lock = RLock()
        self.directory = None
        if directory is not None:
            try:
                target = Path(directory)
                target.mkdir(parents=True, exist_ok=True)
                self.directory = target
            except OSError as exc:
                self.issues.append(f"cache_directory_unavailable:{type(exc).__name__}")

    def get(self, key: Any) -> Optional[dict]:
        digest = stable_hash(key)
        with self._lock:
            if digest in self._values:
                return deepcopy(self._values[digest])
            if self.directory is None:
                return None
            path = self.directory / f"{digest}.json"
            try:
                with path.open("r", encoding="utf-8") as handle:
                    envelope = json.load(handle)
                valid = (
                    isinstance(envelope, dict)
                    and envelope.get("format_version") == self.FORMAT_VERSION
                    and envelope.get("key_hash") == digest
                    and isinstance(envelope.get("value"), dict)
                )
                if not valid:
                    self.issues.append(f"cache_invalid_envelope:{digest}")
                    return None
                # Revalidate strict JSON values, including rejecting NaN/Infinity.
                stable_hash(envelope["value"])
            except FileNotFoundError:
                return None
            except (OSError, ValueError, TypeError, UnicodeError) as exc:
                self.issues.append(f"cache_read_failed:{digest}:{type(exc).__name__}")
                return None
            self._values[digest] = deepcopy(envelope["value"])
            return deepcopy(envelope["value"])

    def put(self, key: Any, value: dict) -> None:
        if not isinstance(value, dict):
            raise TypeError("Scoring cache values must be dictionaries")
        digest = stable_hash(key)
        # Serialize first: unsupported payloads never leave partial memory state.
        envelope = {
            "format_version": self.FORMAT_VERSION,
            "key_hash": digest,
            "value": value,
        }
        content = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        with self._lock:
            self._values[digest] = deepcopy(value)
            if self.directory is None:
                return
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.directory,
                    prefix=f".{digest}.", suffix=".tmp", delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.directory / f"{digest}.json")
            except OSError as exc:
                self.issues.append(f"cache_write_failed:{digest}:{type(exc).__name__}")
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as exc:
                        self.issues.append(f"cache_cleanup_failed:{digest}:{type(exc).__name__}")
