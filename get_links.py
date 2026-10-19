import os, sys, argparse, requests, pyperclip
from bs4 import BeautifulSoup
from datetime import datetime
from colorama import Fore, Style

class console:
    def __init__(self) -> None:
        self.colors = {"green": Fore.GREEN, "red": Fore.RED, "yellow": Fore.YELLOW, "blue": Fore.BLUE, "magenta": Fore.MAGENTA, "cyan": Fore.CYAN, "white": Fore.WHITE, "black": Fore.BLACK, "reset": Style.RESET_ALL, "lightblack": Fore.LIGHTBLACK_EX, "lightred": Fore.LIGHTRED_EX, "lightgreen": Fore.LIGHTGREEN_EX, "lightyellow": Fore.LIGHTYELLOW_EX, "lightblue": Fore.LIGHTBLUE_EX, "lightmagenta": Fore.LIGHTMAGENTA_EX, "lightcyan": Fore.LIGHTCYAN_EX, "lightwhite": Fore.LIGHTWHITE_EX}
    def clear(self):
        os.system("cls" if os.name == "nt" else "clear")
    def timestamp(self):
        return datetime.now().strftime("%H:%M:%S")
    def success(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightgreen']}SUCC {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightgreen']}{obj}{self.colors['white']} {self.colors['reset']}")
    def error(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightred']}ERRR {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightred']}{obj}{self.colors['white']} {self.colors['reset']}")
    def done(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightmagenta']}DONE {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightmagenta']}{obj}{self.colors['white']} {self.colors['reset']}")
    def warning(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightyellow']}WARN {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightyellow']}{obj}{self.colors['white']} {self.colors['reset']}")
    def info(self, message, obj):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightblue']}INFO {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors['lightblue']}{obj}{self.colors['white']} {self.colors['reset']}")
    def custom(self, message, obj, color):
        print(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors[color.upper()]}{color.upper()} {self.colors['lightblack']}• {self.colors['white']}{message} : {self.colors[color.upper()]}{obj}{self.colors['white']} {self.colors['reset']}")
    def input(self, message):
        return input(f"{self.colors['lightblack']}{self.timestamp()} » {self.colors['lightcyan']}INPUT   {self.colors['lightblack']}• {self.colors['white']}{message}{self.colors['reset']}")


log = console()

# Keep the status glyphs printable under legacy Windows code pages (GBK, cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(BASE_DIR, "input.txt")
HOST_PREFIX = "https://fuckingfast.co/"


def extract_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for dlinks_div in soup.find_all("div", class_="dlinks"):
        for anchor in dlinks_div.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.startswith(HOST_PREFIX) and href not in seen:
                seen.add(href)
                links.append(href)
    return links


def read_existing(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return [line.strip() for line in file if line.strip()]
    except FileNotFoundError:
        return []


def write_links(path, links):
    """Rewrite the file in one shot so a crash never leaves it half written."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(links))
        if links:
            file.write("\n")
    os.replace(tmp_path, path)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Extract FuckingFast links from a FitGirl page and write them to input.txt.",
    )
    parser.add_argument("url", nargs="?", help="FitGirl game page URL (prompted when omitted)")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="output file (default: input.txt)")
    parser.add_argument("--append", action="store_true", help="merge with existing entries instead of overwriting")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds (default: 30)")
    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    log.clear()

    url = options.url or log.input("Enter Fitgirl Game Link : ")
    url = url.strip()
    if not url:
        log.error("No URL provided", "nothing to do")
        raise SystemExit(1)

    try:
        r = requests.get(
            url,
            timeout=options.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error("HTTP request failed", f"{url} ({e})")
        raise SystemExit(1)

    links = extract_links(r.text)
    if not links:
        log.error("No Matching URLs Found", "page layout changed or the post has no FuckingFast mirrors")
        raise SystemExit(1)

    output_path = options.input
    if not os.path.isabs(output_path):
        output_path = os.path.join(BASE_DIR, output_path)

    if options.append:
        merged = read_existing(output_path)
        seen = set(merged)
        added = 0
        for link in links:
            if link not in seen:
                merged.append(link)
                seen.add(link)
                added += 1
        final = merged
        log.info("Appended to existing list", f"{added} new / {len(merged)} total")
    else:
        final = links

    write_links(output_path, final)

    print("Matching URLs :")
    print("\n".join(links))
    try:
        pyperclip.copy("\n".join(final))
    except Exception as e:
        log.warning("Clipboard unavailable", str(e))

    log.success("Links written to input file", output_path)
    log.success("All Links Copied To Clipboard", len(final))


if __name__ == "__main__":
    main()
