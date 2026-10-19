import os, re, sys, json, time, argparse, asyncio, requests
import nodriver as uc
from urllib.parse import urlparse
from tqdm import tqdm
from datetime import datetime
from colorama import Fore, Style

from aria2 import Aria2Client, Aria2Error, DEFAULT_HOST as ARIA2_DEFAULT_HOST, DEFAULT_PORT as ARIA2_DEFAULT_PORT
import state
from state import StateStore

try:
    import winreg
except ImportError:  # non-Windows: let nodriver pick the browser
    winreg = None


class console:
    def __init__(self) -> None:
        self.colors = {
            "green": Fore.GREEN, "red": Fore.RED, "yellow": Fore.YELLOW, "blue": Fore.BLUE,
            "magenta": Fore.MAGENTA, "cyan": Fore.CYAN, "white": Fore.WHITE, "black": Fore.BLACK,
            "reset": Style.RESET_ALL, "lightblack": Fore.LIGHTBLACK_EX, "lightred": Fore.LIGHTRED_EX,
            "lightgreen": Fore.LIGHTGREEN_EX, "lightyellow": Fore.LIGHTYELLOW_EX,
            "lightblue": Fore.LIGHTBLUE_EX, "lightmagenta": Fore.LIGHTMAGENTA_EX,
            "lightcyan": Fore.LIGHTCYAN_EX, "lightwhite": Fore.LIGHTWHITE_EX,
        }

    def clear(self):
        os.system("cls" if os.name == "nt" else "clear")

    def timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    def success(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightgreen']}SUCC {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightgreen']}{obj}{self.colors['white']} {self.colors['reset']}")

    def error(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightred']}ERRR {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightred']}{obj}{self.colors['white']} {self.colors['reset']}")

    def warning(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightyellow']}WARN {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightyellow']}{obj}{self.colors['white']} {self.colors['reset']}")

    def info(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightblue']}INFO {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightblue']}{obj}{self.colors['white']} {self.colors['reset']}")

    def done(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightmagenta']}DONE {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightmagenta']}{obj}{self.colors['white']} {self.colors['reset']}")

    def input(self, message):
        return input(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightcyan']}INPUT   {self.colors['lightblack']}• {self.colors['white']}{message}{self.colors['reset']}")


log = console()

# The status glyphs are not representable in legacy Windows code pages (GBK, cp1252),
# where printing them raises UnicodeEncodeError and kills the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(BASE_DIR, "input.txt")
DEFAULT_STATE = os.path.join(BASE_DIR, "state.json")
DEFAULT_DOWNLOAD_ROOT = os.path.join(BASE_DIR, "downloads")
DEFAULT_RECEIPTS = os.path.join(BASE_DIR, "aria2_receipts.jsonl")

CF_WAIT_SECS = 120
TURNSTILE_WAIT_SECS = 25
CLICK_WIDGET_AFTER_SECS = 7
PROFILE_DIR = os.path.join(BASE_DIR, ".temp_browser_profile")

INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_file_name(name, fallback="download.bin"):
    """Turn a URL fragment into a name Windows will actually accept."""
    cleaned = INVALID_NAME_CHARS.sub("_", name or "").strip().strip(".")
    if not cleaned:
        return fallback
    stem, _, ext = cleaned.rpartition(".")
    if stem.upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 180:
        base, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 10:
            cleaned = base[: 180 - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:180]
    return cleaned


def get_default_browser():
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")

        command_paths = [
            rf"Software\Classes\{prog_id}\shell\open\command",
            rf"Software\Classes\{prog_id}\Application",
        ]

        for path in command_paths:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    path
                ) as key:
                    command, _ = winreg.QueryValueEx(key, "")
                    if command:
                        match = re.match(r'"([^"]+\.exe)"', command)

                        if match:
                            exe = match.group(1)
                            if os.path.isfile(exe):
                                return exe

                        match = re.match(r'(.+?\.exe)', command, re.IGNORECASE)
                        if match:
                            exe = match.group(1).strip('"')
                            if os.path.isfile(exe):
                                return exe

            except FileNotFoundError:
                pass

        for path in command_paths:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    path
                ) as key:
                    command, _ = winreg.QueryValueEx(key, "")

                    if command:
                        match = re.match(r'"([^"]+\.exe)"', command)

                        if match:
                            exe = match.group(1)
                            if os.path.isfile(exe):
                                return exe

            except FileNotFoundError:
                pass

    except Exception as e:
        log.warning("Could not detect default browser", str(e))

    return None


def resolve_output_path(downloads_folder, file_name):
    return os.path.join(downloads_folder, file_name)


def _open_stream(download_url, existing_size):
    headers = {"Range": f"bytes={existing_size}-"} if existing_size > 0 else {}
    return requests.get(download_url, stream=True, headers=headers, timeout=(15, 120))


def download_file(download_url, output_path):
    """Stream one direct link to disk, resuming from whatever is already there.

    Returns (ok, total_size) so the caller can record the expected size in the
    state file instead of guessing later.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    existing_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0

    try:
        response = _open_stream(download_url, existing_size)
    except requests.RequestException as e:
        log.error("Failed to download file", str(e))
        return False, None

    block_size = 8192

    if response.status_code == 416:
        log.success("File already complete", os.path.basename(output_path))
        return True, existing_size or None

    if response.status_code == 206:
        content_range = response.headers.get("Content-Range", "")
        match = re.search(r"/(\d+)$", content_range)
        total_size = int(match.group(1)) if match else existing_size + int(response.headers.get("content-length", 0))
        start_match = re.match(r"bytes\s+(\d+)-", content_range)
        if start_match and int(start_match.group(1)) != existing_size:
            # Appending a range that does not line up with the local file would
            # silently corrupt it, so start over from byte zero instead.
            log.warning("Server resumed from an unexpected offset", f"{content_range} (local {existing_size})")
            response.close()
            try:
                os.remove(output_path)
            except OSError:
                pass
            try:
                response = _open_stream(download_url, 0)
            except requests.RequestException as e:
                log.error("Failed to download file", str(e))
                return False, None
            if response.status_code != 200:
                log.error("Failed to restart download", response.status_code)
                return False, None
            total_size = int(response.headers.get("content-length", 0))
            mode = "wb"
            initial = 0
        elif existing_size >= total_size > 0:
            log.success("File already complete", os.path.basename(output_path))
            return True, total_size
        else:
            mode = "ab"
            initial = existing_size
            log.info("Resuming download", f"{os.path.basename(output_path)} from {existing_size} bytes")
    elif response.status_code == 200:
        total_size = int(response.headers.get("content-length", 0))
        if existing_size > 0 and total_size and existing_size >= total_size:
            log.success("File already complete", os.path.basename(output_path))
            return True, total_size
        if existing_size > 0:
            log.warning("Server ignored Range, re-downloading", os.path.basename(output_path))
        mode = "wb"
        initial = 0
    else:
        log.error("Failed to download file", response.status_code)
        return False, None

    bar = tqdm(
        total=total_size,
        initial=initial,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=f"{log.colors['lightblue']}Downloading{log.colors['reset']} {os.path.basename(output_path)[:55]}",
    )
    try:
        with open(output_path, mode) as f, bar:
            for data in response.iter_content(block_size):
                if not data:
                    continue
                f.write(data)
                bar.update(len(data))
    except (requests.RequestException, OSError) as e:
        log.error("Download interrupted", f"{os.path.basename(output_path)} ({e})")
        return False, total_size or None

    final_size = os.path.getsize(output_path)
    if total_size and final_size < total_size:
        log.error("Download incomplete", f"{final_size}/{total_size} bytes")
        return False, total_size

    short = f"{output_path[:35]}...{output_path[55:]}" if len(output_path) > 55 else output_path
    log.success("Successfully downloaded file", short)
    return True, final_size


def file_id_from_link(link):
    return urlparse(link).path.strip("/").split("/")[-1]


async def js(tab, expression, await_promise=False):
    """Evaluate JS that returns a JSON string, and decode it.

    nodriver hands back its internal RemoteObject whenever a result is falsy,
    which reads as truthy in Python, so every result is JSON encoded in the
    page and decoded here.
    """
    raw = await tab.evaluate(
        expression,
        await_promise=await_promise,
        return_by_value=True,
    )
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def page_ready(tab):
    """True once Cloudflare interstitial is gone and download page loaded."""
    state = await js(
        tab,
        """(() => {
            const title = document.title || '';
            const body = (document.body && document.body.innerText) || '';
            const hasChallenge = title.includes('Just a moment')
                || body.includes('Verifying you are human')
                || !!document.querySelector('#challenge-running, #cf-challenge-running, .cf-browser-verification');
            const hasDownload = !!document.querySelector('a.link-button')
                || !!document.querySelector('#cf-turnstile')
                || !!document.querySelector('meta[name="title"]')
                || /DOWNLOAD/i.test(body);
            return JSON.stringify({ hasChallenge, hasDownload, title });
        })()""",
    )
    if not isinstance(state, dict):
        return False
    return bool(not state.get("hasChallenge") and state.get("hasDownload"))


async def wait_for_cf_clear(tab):
    log.info("Waiting for Cloudflare check", "solve/wait in the browser window if needed")
    last_err = None
    for i in range(CF_WAIT_SECS):
        try:
            if await page_ready(tab):
                log.success("Cloudflare cleared", f"after {i + 1}s")
                return True
        except Exception as e:
            last_err = e
        await asyncio.sleep(1)
        if i and i % 15 == 0:
            extra = f" ({last_err})" if last_err else ""
            log.info("Still waiting for download page", f"{i}s{extra}")
    return False


async def has_turnstile_widget(tab):
    """Trusted sessions get served the page without a captcha widget at all."""
    return await js(tab, "JSON.stringify(!!document.getElementById('cf-turnstile'))") is True


async def click_turnstile_widget(tab):
    """Nudge an interactive Turnstile checkbox that is waiting on a real click."""
    try:
        widget = await tab.select("#cf-turnstile", timeout=2)
        await widget.mouse_click()
        return True
    except Exception:
        return False


async def wait_for_turnstile(tab):
    """Wait for a fresh Turnstile token; it is single use per /go request."""
    log.info("Waiting for Turnstile token", "click checkbox if shown")
    clicked = False
    for i in range(TURNSTILE_WAIT_SECS):
        try:
            token = await js(
                tab,
                """JSON.stringify(
                    window.turnstileToken
                    || document.querySelector('[name="cf-turnstile-response"]')?.value
                    || ''
                )""",
            )
            if isinstance(token, str) and len(token) > 20:
                log.success("Turnstile ready", f"after {i + 1}s")
                return token
        except Exception:
            pass

        if not clicked and i == CLICK_WIDGET_AFTER_SECS:
            clicked = await click_turnstile_widget(tab)
            if clicked:
                log.info("Clicked Turnstile widget", "waiting for token")

        await asyncio.sleep(1)
    return None


async def post_go(tab, file_id, token):
    """Ask /go for the direct link from inside the cleared browser session."""
    return await js(
        tab,
        f"""
        (async () => {{
            const response = await fetch('/f/{file_id}/go', {{
                method: 'POST',
                headers: {{
                    'content-type': 'application/x-www-form-urlencoded',
                    'hx-request': 'true',
                    'hx-current-url': location.href,
                    'referer': location.href,
                }},
                body: new URLSearchParams({{ 'cf-turnstile-response': {json.dumps(token)} }}),
            }});
            const redirect = response.headers.get('HX-Redirect')
                || response.headers.get('hx-redirect');
            const body = await response.text();
            return JSON.stringify({{
                status: response.status,
                redirect: redirect,
                body: body.slice(0, 200),
            }});
        }})()
        """,
        await_promise=True,
    )


async def resolve_direct_url(tab, link, attempts=4):
    """Reloading the page each attempt gives Turnstile a fresh widget to solve."""
    file_id = file_id_from_link(link)
    last_error = "unknown error"

    for attempt in range(1, attempts + 1):
        await tab.get(link)

        if not await wait_for_cf_clear(tab):
            last_error = "Stuck on Cloudflare 'Just a moment' page"
        else:
            if await has_turnstile_widget(tab):
                token = await wait_for_turnstile(tab)
            else:
                log.info("No captcha widget on page", "session already trusted")
                token = ""

            if token is None:
                last_error = "Turnstile token not ready"
            else:
                result = await post_go(tab, file_id, token)
                if not isinstance(result, dict):
                    last_error = f"Unexpected browser response: {result!r}"
                elif result.get("redirect"):
                    return result["redirect"], None
                else:
                    last_error = f"No HX-Redirect ({result.get('body') or result.get('status')})"

        if attempt < attempts:
            log.warning("Retrying link", f"attempt {attempt + 1}/{attempts} — {last_error}")
            await asyncio.sleep(3)

    return None, last_error


async def shutdown_browser(browser):
    """Close browser and let its pipes drain before the event loop goes away."""
    try:
        browser.stop()
    except Exception:
        pass

    process = getattr(getattr(browser, "config", None), "browser_process", None)
    if process is not None:
        for _ in range(20):
            if process.poll() is not None:
                break
            await asyncio.sleep(0.1)
        else:
            try:
                process.kill()
            except Exception:
                pass

    # Give the transports a turn of the loop to close themselves.
    await asyncio.sleep(0.5)


def start_aria2(options):
    """Return a live RPC client, or None when the endpoint is not there."""
    client = Aria2Client(
        host=options.aria2_host,
        port=options.aria2_port,
        secret=options.aria2_secret,
    )
    try:
        version = client.get_version()
    except Aria2Error as e:
        log.error("aria2 RPC unavailable", str(e))
        return None
    log.info("aria2 RPC connected", f"{options.aria2_host}:{options.aria2_port} (v{version})")
    return client


def choose_method(options, interactive):
    """Resolve the download backend, asking the user when it was not passed."""
    if options.method:
        return options.method

    if not interactive:
        log.info("Download method", "direct (no interactive console, pass --method to change)")
        return "direct"

    log.info("Download method", "1 = direct (built-in)  |  2 = aria2 (RPC)")
    while True:
        try:
            answer = log.input("Select download method [1/2] (default 1) : ").strip().lower()
        except EOFError:
            return "direct"
        if answer in ("", "1", "d", "direct"):
            return "direct"
        if answer in ("2", "a", "aria2"):
            return "aria2"
        log.warning("Invalid choice", f"{answer!r} — enter 1 or 2")


def confirm(question):
    try:
        answer = log.input(f"{question} [Y/n] : ").strip().lower()
    except EOFError:
        return False
    return answer in ("", "y", "yes")


def describe_progress(gid, status):
    total = int(status.get("totalLength") or 0)
    done = int(status.get("completedLength") or 0)
    if total:
        return f"{gid[:8]} {done * 100 / total:.1f}% ({done}/{total})"
    return f"{gid[:8]} {done} bytes"


def resolve_action(store, aria2, link, path, force):
    """Decide whether a link still needs work, asking aria2 only when needed.

    Returns (action, entry) where action is one of state.SKIP / IN_FLIGHT / RETRY.
    """
    if force:
        return state.RETRY, store.entry(link)

    action = store.classify(link, path)
    if action != state.IN_FLIGHT:
        return action, store.entry(link)

    entry = store.entry(link)
    gid = entry.get("gid")
    if aria2 is None or not gid:
        return state.RETRY, entry

    if aria2.is_alive(gid):
        return state.IN_FLIGHT, entry

    log.warning("aria2 no longer knows this gid", f"{gid} — will resolve again")
    return state.RETRY, entry


def apply_receipts(store, receipt_path):
    """Fold aria2 completion hooks into state before doing any work."""
    records = state.read_receipts(receipt_path)
    if not records:
        return 0
    applied = 0
    for record in records:
        if store.apply_receipt(record):
            applied += 1
    if applied:
        log.info("Applied aria2 receipts", f"{applied} of {len(records)}")
    return applied


async def run(links, downloads_folder, options):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    log.info("Launching browser", "nodriver + persistent profile")

    browser_executable = get_default_browser()

    if browser_executable:
        log.info("Default browser detected", browser_executable)
    else:
        log.warning("Default browser not detected", "letting nodriver choose")

    if options.headless:
        log.warning("Headless mode", "if Turnstile demands a click, re-run without --headless")

    browser = await uc.start(
        headless=options.headless,
        user_data_dir=PROFILE_DIR,
        browser_executable_path=browser_executable,
        browser_args=[
            "--window-size=900,700",
            "--disable-popup-blocking",
        ],
    )

    tab = await browser.get("about:blank")

    store = StateStore(options.state)
    aria2 = getattr(options, "aria2_client", None)
    forwarded = {}
    skipped = 0
    failures = 0

    try:
        for index, link in enumerate(links, 1):
            log.info("Started processing", f"[{index}/{len(links)}] {link[:30]}...{link[60:]}" if len(link) > 60 else f"[{index}/{len(links)}] {link}")

            fragment_name = urlparse(link).fragment.strip()
            file_name = safe_file_name(fragment_name, "default_file_name")
            output_path = resolve_output_path(downloads_folder, file_name)

            action, entry = resolve_action(store, aria2, link, output_path, options.force)
            if action == state.SKIP:
                log.success("Skipping finished file", file_name)
                skipped += 1
                continue
            if action == state.IN_FLIGHT:
                log.info("Already downloading in aria2", f"{file_name} (gid {entry.get('gid')})")
                forwarded[link] = entry["gid"]
                skipped += 1
                continue

            try:
                download_url, err = await resolve_direct_url(tab, link)
            except Exception as e:
                log.error("Browser resolve failed", str(e))
                store.mark_failed(link, e, name=file_name, method=options.method)
                failures += 1
                continue

            if not download_url:
                log.error("Failed to get download url", err)
                store.mark_failed(link, err, name=file_name, method=options.method)
                failures += 1
                continue

            log.info("Fetched download url", f"{download_url[:70]}...")

            if aria2 is not None:
                # Resolve one link, hand it to aria2 immediately, then move on.
                try:
                    gid = aria2.add_uri(
                        download_url,
                        directory=os.path.abspath(downloads_folder),
                        file_name=file_name,
                        split=options.aria2_split,
                    )
                except Aria2Error as e:
                    if "exist" in str(e).lower() and os.path.isfile(output_path):
                        # aria2 refuses to overwrite: the file is already there.
                        store.mark_done(link, file_name, output_path, method="aria2")
                        log.success("File already on disk", file_name)
                        skipped += 1
                    else:
                        log.error("aria2 rejected link", str(e))
                        store.mark_failed(link, e, name=file_name, method="aria2")
                        failures += 1
                    continue
                forwarded[link] = gid
                store.mark_forwarded(link, file_name, gid, output_path, method="aria2")
                log.success("Forwarded to aria2", f"{file_name} -> gid {gid}")
            else:
                try:
                    ok, total = download_file(download_url, output_path)
                except Exception as e:
                    log.error("Failed to download file", str(e))
                    store.mark_failed(link, e, name=file_name, method="direct")
                    failures += 1
                    continue
                if ok:
                    store.mark_done(link, file_name, output_path, method="direct", size=total)
                else:
                    store.mark_failed(link, "incomplete download", name=file_name, method="direct")
                    failures += 1

            await asyncio.sleep(1)
    finally:
        await shutdown_browser(browser)

    if aria2 is None:
        return failures

    if not options.wait:
        report_forwarded(forwarded, downloads_folder, options)
        return failures

    return failures + wait_for_aria2(store, aria2, forwarded, downloads_folder, options)


def report_forwarded(forwarded, downloads_folder, options):
    """Print what was handed to aria2 and stop; aria2 keeps downloading."""
    if not forwarded:
        log.info("Nothing new forwarded", f"{downloads_folder}")
        return
    log.done("Forwarded", f"{len(forwarded)} job(s) to aria2 on port {options.aria2_port}")
    for link, gid in forwarded.items():
        name = safe_file_name(urlparse(link).fragment.strip(), "default_file_name")
        log.info("  job", f"{name} -> {gid}")
    log.info("Download folder", os.path.abspath(downloads_folder))
    log.info("aria2 keeps running", "progress: aria2.tellStatus <gid>, or configure aria2_hook.py receipts")
    log.info("Re-run later", "finished files are detected on disk and skipped")


def wait_for_aria2(store, aria2, forwarded, downloads_folder, options):
    """Poll until every forwarded gid stops moving; return the failure count."""
    log.info("Waiting for aria2", f"{len(forwarded)} job(s) on port {options.aria2_port}")
    last_report = {}

    def on_event(kind, gid, status):
        if kind != "progress":
            return
        now = time.monotonic()
        if now - last_report.get(gid, 0) < 15:
            return
        last_report[gid] = now
        log.info("aria2 progress", describe_progress(gid, status))

    final = aria2.wait(list(forwarded.values()), poll=2.0, on_event=on_event)
    by_gid = {gid: link for link, gid in forwarded.items()}
    failures = 0
    for gid, status in final.items():
        link = by_gid.get(gid)
        if link is None:
            continue
        name = safe_file_name(urlparse(link).fragment.strip(), "default_file_name")
        path = os.path.join(downloads_folder, name)
        if status.get("status") == "complete" and os.path.isfile(path):
            size = int(status.get("totalLength") or 0) or None
            store.mark_done(link, name, path, method="aria2", gid=gid, size=size)
            log.success("aria2 finished", name)
        elif status.get("status") == "complete":
            # aria2 says done but the file is not where we asked for it:
            # usually a renamed target or a different --dir.
            reason = f"aria2 reported complete but {path} is missing"
            store.mark_failed(link, reason, name=name, method="aria2")
            log.error("aria2 finished elsewhere", reason)
            failures += 1
        else:
            reason = status.get("errorMessage") or status.get("status") or "unknown"
            store.mark_failed(link, reason, name=name, method="aria2")
            log.error("aria2 failed", f"{name} ({reason})")
            failures += 1
    return failures


def silence_pipe_teardown_noise(unraisable):
    """Chrome's pipes are collected after the loop closes, which asyncio reports
    from __del__ as an unraisable error. Nothing actionable, so drop only those."""
    message = str(unraisable.exc_value)
    if isinstance(unraisable.exc_value, (ValueError, RuntimeError)) and (
        "closed pipe" in message or "Event loop is closed" in message
    ):
        return
    sys.__unraisablehook__(unraisable)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Resolve FuckingFast links through a real browser, then download them directly or via aria2.",
    )
    parser.add_argument("--method", choices=("direct", "aria2"), default=None,
                        help="download backend; asked interactively when omitted")
    parser.add_argument("--no-prompt", action="store_true",
                        help="never ask questions; falls back to --method direct")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="link list, one per line (default: input.txt)")
    parser.add_argument("--output", default=None, help="download folder (default: downloads/<game-name>)")
    parser.add_argument("--state", default=DEFAULT_STATE, help="state file (default: state.json)")
    parser.add_argument("--limit", type=int, default=0, help="process at most N links (0 = all)")
    parser.add_argument("--force", action="store_true", help="ignore recorded state and redo every link")
    parser.add_argument("--headless", action="store_true", help="run the browser without a window")
    parser.add_argument("--aria2-host", default=ARIA2_DEFAULT_HOST, help="aria2 RPC host (default: 127.0.0.1)")
    parser.add_argument("--aria2-port", type=int, default=ARIA2_DEFAULT_PORT, help="aria2 RPC port (default: 16800)")
    parser.add_argument("--aria2-secret", default=os.environ.get("ARIA2_SECRET", ""), help="aria2 RPC secret token")
    parser.add_argument("--aria2-split", type=int, default=4, help="connections per file for aria2 (default: 4)")
    parser.add_argument("--wait", action="store_true",
                        help="keep polling aria2 until every job finishes (default: exit after forwarding)")
    parser.add_argument("--receipts", default=DEFAULT_RECEIPTS,
                        help="aria2 hook receipts to fold into state (default: aria2_receipts.jsonl)")
    return parser


def main(argv=None):
    sys.unraisablehook = silence_pipe_teardown_noise
    options = build_parser().parse_args(argv)

    interactive = sys.stdin is not None and sys.stdin.isatty() and not options.no_prompt
    options.interactive = interactive
    options.method = choose_method(options, interactive)

    input_path = options.input
    if not os.path.isabs(input_path):
        input_path = os.path.join(BASE_DIR, input_path)

    if not os.path.isabs(options.state):
        options.state = os.path.join(BASE_DIR, options.state)

    try:
        with open(input_path, "r", encoding="utf-8") as file:
            links = [line.strip() for line in file if line.strip()]
    except FileNotFoundError:
        log.error("Input file not found", input_path)
        log.warning("Run get_links.py first", "it writes input.txt for you")
        raise SystemExit(1)

    if not links:
        log.warning("Input file is empty", "run get_links.py to fill it")
        raise SystemExit(1)

    if options.limit > 0:
        links = links[: options.limit]

    options.aria2_client = None
    if options.method == "aria2":
        options.aria2_client = start_aria2(options)
        if options.aria2_client is None:
            if options.interactive and confirm("aria2 is not reachable — fall back to direct download?"):
                log.warning("Falling back", "direct download selected")
                options.method = "direct"
            else:
                log.error("Aborted", "start aria2c with --enable-rpc, or re-run without --method aria2")
                raise SystemExit(1)

    if options.output:
        downloads_folder = os.path.abspath(options.output)
    else:
        first_game_link = next((l for l in links if "fitgirl-repacks.site" in urlparse(l).fragment), None)
        if not first_game_link:
            first_game_link = links[0]

        fragment = urlparse(first_game_link).fragment
        if "fitgirl-repacks.site" in fragment:
            game_name = safe_file_name(fragment.split("--")[0].strip("_"), "downloads")
        else:
            game_name = "downloads"
        downloads_folder = os.path.join(DEFAULT_DOWNLOAD_ROOT, game_name)

    os.makedirs(downloads_folder, exist_ok=True)
    log.info("Download folder", downloads_folder)
    log.info("Links to process", len(links))
    log.info("Download method", options.method)

    receipt_path = options.receipts
    if not os.path.isabs(receipt_path):
        receipt_path = os.path.join(BASE_DIR, receipt_path)
    apply_receipts(StateStore(options.state), receipt_path)

    try:
        failures = asyncio.run(run(links, downloads_folder, options))
    except KeyboardInterrupt:
        log.warning("Interrupted", "partial files and state are kept; re-run to resume")
        raise SystemExit(130)

    log.done("Finished", downloads_folder)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
