import json
import os
import tempfile
from datetime import datetime

DONE = "done"
FORWARDED = "forwarded"
FAILED = "failed"

# What the caller should do with one entry.
SKIP = "skip"          # finished, nothing to do
IN_FLIGHT = "in_flight"  # handed to aria2 and still moving
RETRY = "retry"        # not finished and nothing is working on it


class StateStore:
    """Per-link bookkeeping, so input.txt can stay untouched.

    Keys are the original FuckingFast links; values record what happened to
    them (done / forwarded to aria2 / failed) plus enough detail to decide
    whether a file on disk is actually the finished download.
    """

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            data = {}
        except (ValueError, OSError):
            data = {}
        self.data = data if isinstance(data, dict) else {}

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        handle, tmp_path = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(self.data, file, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def entry(self, link):
        entry = self.data.get(link)
        return entry if isinstance(entry, dict) else {}

    def _size_matches(self, entry, path):
        if not os.path.isfile(path):
            return False
        expected = entry.get("size")
        if not expected:
            return True
        return os.path.getsize(path) == expected

    def is_complete(self, link, path):
        entry = self.entry(link)
        if entry.get("status") not in (DONE, FORWARDED):
            return False
        return self._size_matches(entry, path)

    def classify(self, link, path):
        """Decide what to do with one link without asking aria2.

        `forwarded` plus a file of the right size is treated as finished, which
        is what makes "forward and quit" safe to re-run. `forwarded` without the
        file still needs a liveness check, so the caller gets IN_FLIGHT only
        after confirming with aria2 (see main.resolve_action).
        """
        entry = self.entry(link)
        status = entry.get("status")

        if status == DONE:
            return SKIP if self._size_matches(entry, path) else RETRY

        if status == FORWARDED:
            if self._size_matches(entry, path):
                return SKIP
            return IN_FLIGHT if entry.get("gid") else RETRY

        return RETRY

    def _update(self, link, **fields):
        entry = dict(self.entry(link))
        entry.update(fields)
        entry["updated"] = datetime.now().isoformat(timespec="seconds")
        self.data[link] = entry
        self.save()

    def mark_done(self, link, name, path, method="direct", gid=None, size=None):
        if size is None:
            size = os.path.getsize(path) if os.path.isfile(path) else None
        self._update(link, name=name, path=path, size=size, method=method, gid=gid,
                     status=DONE, error=None)

    def mark_forwarded(self, link, name, gid, path, method="aria2"):
        self._update(link, name=name, path=path, method=method, gid=gid,
                     status=FORWARDED, error=None,
                     forwarded_at=datetime.now().isoformat(timespec="seconds"))

    def mark_failed(self, link, error, name=None, method=None):
        entry = dict(self.entry(link))
        fields = {"status": FAILED, "error": str(error)}
        if name:
            fields["name"] = name
        if method:
            fields["method"] = method
        entry.update(fields)
        entry["updated"] = datetime.now().isoformat(timespec="seconds")
        self.data[link] = entry
        self.save()

    def apply_receipt(self, record):
        """Fold one aria2 completion receipt into the state file.

        Returns the link it updated, or None when no pending entry matches.
        """
        gid = record.get("gid")
        if not gid:
            return None
        for link, entry in self.data.items():
            if entry.get("gid") != gid or entry.get("status") != FORWARDED:
                continue
            name = entry.get("name")
            if record.get("event") == "complete":
                path = record.get("path") or entry.get("path")
                size = record.get("size")
                self.mark_done(link, name, path, method="aria2", gid=gid, size=size)
            else:
                self.mark_failed(link, record.get("error") or "aria2 reported an error",
                                 name=name, method="aria2")
            return link
        return None


def read_receipts(path):
    """Read the jsonl written by aria2_hook.py; bad lines are skipped."""
    records = []
    try:
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return records
