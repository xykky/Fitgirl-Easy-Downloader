"""aria2 completion hook: append one JSON line per finished download.

aria2 invokes this through its own command-line hooks, which is why it runs as
a separate process and cannot share state with main.py:

    aria2c --enable-rpc --rpc-listen-port=16800 \
           --on-download-complete="python C:\\path\\aria2_hook.py complete" \
           --on-download-error="python C:\\path\\aria2_hook.py error"

Arguments after the event name are the ones aria2 appends:
  complete: GID NUM_FILES FILE_PATH
  error:    GID NUM_FILES FILE_PATH ERROR_CODE ERROR_MESSAGE
  stop:     GID NUM_FILES FILE_PATH

main.py reads the resulting jsonl on its next run and folds it into state.json.
"""

import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RECEIPT = os.path.join(BASE_DIR, "aria2_receipts.jsonl")
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def receipt_path():
    return os.environ.get("FITGIRL_RECEIPT") or DEFAULT_RECEIPT


def parse(event, argv):
    """Map aria2's positional arguments onto a receipt record."""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": event,
    }
    if len(argv) >= 1:
        record["gid"] = argv[0]
    if len(argv) >= 2:
        record["files"] = argv[1]
    if len(argv) >= 3:
        record["path"] = argv[2]
        try:
            record["size"] = os.path.getsize(argv[2])
        except OSError:
            record["size"] = None
    if event == "error":
        if len(argv) >= 4:
            record["error_code"] = argv[3]
        if len(argv) >= 5:
            record["error"] = argv[4]
    return record


def append(record, path):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    # Keep the file from growing forever; one rename is enough, and a crash
    # between the two steps only costs the old receipts.
    try:
        if os.path.getsize(path) > MAX_RECEIPT_BYTES:
            os.replace(path, path + ".old")
    except OSError:
        pass

    line = json.dumps(record, ensure_ascii=False) + "\n"
    # O_APPEND keeps concurrent hooks from interleaving their lines.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def main(argv):
    if not argv:
        print("usage: aria2_hook.py {complete|error|stop} GID NUM_FILES FILE_PATH [...]",
              file=sys.stderr)
        return 2

    event = argv[0].strip().lower()
    if event not in ("complete", "error", "stop"):
        print(f"unknown event: {event!r}", file=sys.stderr)
        return 2

    record = parse(event, argv[1:])
    append(record, receipt_path())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
