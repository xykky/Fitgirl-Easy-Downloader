import json
import time
import urllib.error
import urllib.request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 16800

STATUS_KEYS = ("gid", "status", "totalLength", "completedLength", "errorMessage", "files")

RUNNING_STATES = ("active", "waiting", "paused")


class Aria2Error(RuntimeError):
    """Raised when the aria2 JSON-RPC endpoint is unreachable or rejects a call.

    `status` is the HTTP status when aria2 answered at all; it stays None when
    the connection never happened, which is what lets callers tell "aria2 is not
    running" apart from "aria2 refused this request".
    """

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def unreachable(self):
        return self.status is None


class Aria2Client:
    """Minimal aria2 JSON-RPC client: enough to forward links and poll them.

    Only the standard library is used, so adding aria2 support costs no new
    pip dependency on the download side.
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, secret="", timeout=10):
        self.host = host
        self.port = port
        self.secret = secret or ""
        self.timeout = timeout
        self._request_id = 0
        # aria2 lives on loopback; never let an HTTP(S)_PROXY env var hijack it.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def endpoint(self):
        return f"http://{self.host}:{self.port}/jsonrpc"

    def _call(self, method, params):
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": ([f"token:{self.secret}"] if self.secret else []) + list(params),
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            # aria2 rejected the call; its own explanation is in the body, and
            # dropping it turns every 400 into the useless "Bad Request".
            try:
                detail = error.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            raise Aria2Error(
                f"aria2 refused {method} with HTTP {error.code}"
                + (f": {detail[:400]}" if detail else " (empty response body)"),
                status=error.code,
                body=detail,
            ) from error
        except urllib.error.URLError as error:
            raise Aria2Error(
                f"cannot reach aria2 RPC at {self.endpoint} ({error.reason}); "
                "start aria2c with --enable-rpc"
            ) from error
        except OSError as error:
            raise Aria2Error(f"cannot reach aria2 RPC at {self.endpoint} ({error})") from error

        try:
            body = json.loads(raw)
        except ValueError:
            raise Aria2Error(
                f"invalid aria2 RPC response from {self.endpoint}: {raw[:400]!r}"
            ) from None

        if not isinstance(body, dict):
            raise Aria2Error(f"unexpected aria2 RPC payload: {body!r}")
        if "error" in body:
            detail = body.get("error") or {}
            raise Aria2Error(
                f"{method} rejected: {detail.get('message', 'unknown error')} "
                f"(code {detail.get('code', '?')})"
            )
        return body.get("result")

    def get_version(self):
        result = self._call("aria2.getVersion", [])
        return result.get("version") if isinstance(result, dict) else None

    def add_uri(self, url, directory, file_name, split=4, timeout=None):
        options = {
            "dir": directory,
            "out": file_name,
            "continue": "true",
            "auto-file-renaming": "false",
            "allow-overwrite": "false",
            "split": str(max(1, int(split))),
            "max-connection-per-server": str(max(1, int(split))),
        }
        if timeout is not None:
            options["timeout"] = str(int(timeout))
        gid = self._call("aria2.addUri", [[url], options])
        if not isinstance(gid, str):
            raise Aria2Error(f"aria2 did not return a gid: {gid!r}")
        return gid

    def tell_status(self, gid, keys=None):
        """Fetch one gid's status.

        Some aria2 builds reject the optional `keys` argument outright, so a
        rejected call is retried once without it before giving up.
        """
        params = [gid] + ([list(keys)] if keys else [])
        try:
            result = self._call("aria2.tellStatus", params)
        except Aria2Error as error:
            if not keys or error.unreachable:
                raise
            result = self._call("aria2.tellStatus", [gid])
        return result if isinstance(result, dict) else {}

    def global_stat(self):
        result = self._call("aria2.getGlobalStat", [])
        return result if isinstance(result, dict) else {}

    def is_alive(self, gid, timeout=3):
        """True when aria2 still knows this gid (running or finished)."""
        original = self.timeout
        self.timeout = timeout
        try:
            self.tell_status(gid)
            return True
        except Aria2Error:
            return False
        finally:
            self.timeout = original

    def wait(self, gids, poll=2.0, on_event=None):
        """Block until every gid stops moving; return the final status per gid."""
        pending = list(dict.fromkeys(gids))
        final = {}
        while pending:
            still_running = []
            for gid in pending:
                try:
                    status = self.tell_status(gid)
                except Aria2Error as error:
                    final[gid] = {"status": "error", "errorMessage": str(error)}
                    continue
                state = status.get("status")
                if state in RUNNING_STATES:
                    still_running.append(gid)
                    if on_event:
                        on_event("progress", gid, status)
                    continue
                final[gid] = status
                if on_event:
                    on_event("stopped", gid, status)
            pending = still_running
            if pending:
                time.sleep(poll)
        return final
