"""
One web page or PDF as text (GT-4): WebFetchTool. HTML through BeautifulSoup (scripts, styles and page furniture left out), a PDF's own
text layer through pypdf (the file is kept in workspace/.automc/web/downloads/<host>/, where DocumentParseTool can make a knowledge base
of it, figures included), plain text, JSON, XML and CSV as they are. Paged: at most max_chars characters per call, from `start`.

Only the public internet: a URL whose host is this machine, a private network, a link-local or reserved address is refused, and so is a
redirect to one (each hop is checked), so a model cannot read the Ollama server, a router page or a facility intranet through it.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import automc_paths
from tools.files.places import Refused

from .search import WEB

DEFAULT_CHARS, MAX_CHARS = 20_000, 50_000
MAX_BYTES = 50_000_000
TIMEOUT = 30
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/plain", "text/csv", "text/markdown", "application/json", "application/xml", "text/xml")
BLOCKS = ["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table", "ul", "ol", "dl", "dt", "dd", "blockquote",
          "pre", "figure", "figcaption", "caption"]


def check_url(url):
    """(url, host) of a public http(s) URL; Refused for anything else."""
    if not isinstance(url, str) or not url.strip():
        raise Refused("url", "must be an http or https address", "InvalidArgument", 2)
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Refused("url", f"must be an http or https address, got {url[:100]!r}", "InvalidArgument", 2)
    try:
        addresses = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))}
    except OSError:
        raise Refused("url", f"{u.hostname} cannot be found (no such host, or no network)", "Unavailable", 5) from None
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise Refused("url", f"{u.hostname} is a local or private address ({ip}); the web tools read only the public internet",
                          "NotAllowed", 3)
    return url.strip(), u.hostname


def _get(url):
    """The response for a URL, following redirects by hand so each hop is checked."""
    import requests
    from tools import conversation
    from tools.chats import store as chat_store
    for _ in range(MAX_REDIRECTS + 1):
        url, _host = check_url(url)
        chat_store.egress(f"web page ({_host})", conversation.label(), chat=(conversation.current() or {}).get("thread_id"),
                          what="a page request")
        try:
            r = requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False,
                             headers={"User-Agent": "Mozilla/5.0 (compatible; AutoMC)"})
        except Exception as e:
            raise Refused("url", f"the request failed ({type(e).__name__}: {str(e)[:150]})", "Unavailable", 5) from None
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("location", ""))
            r.close()
            continue
        return url, r
    raise Refused("url", f"more than {MAX_REDIRECTS} redirects", "Unavailable", 5)


def _body(r):
    data = bytearray()
    for block in r.iter_content(1 << 16):
        data += block
        if len(data) > MAX_BYTES:
            r.close()
            raise Refused("url", f"the response is over {MAX_BYTES / 1e6:.0f} MB", "NotAllowed", 3)
    return bytes(data)


def _html_text(raw, encoding):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser", from_encoding=encoding)
    title = soup.title.get_text(strip=True) if soup.title else None
    for tag in soup(["script", "style", "noscript", "template", "svg", "nav", "footer", "aside", "form", "header"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo", "search"]}):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup
    for br in main.find_all("br"):                           # lines break only where the page breaks them; inline text stays joined
        br.replace_with("\n")
    for cell in main.find_all(["td", "th"]):
        cell.append(" | ")
    for block in main.find_all(BLOCKS):
        block.append("\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", line.replace("\xad", "")).strip(" |") for line in main.get_text("").splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return title, text


def _save_pdf(raw, url, host):
    folder = Path(automc_paths.WORKSPACE).resolve() / WEB / "downloads" / re.sub(r"[^A-Za-z0-9._-]+", "_", host)
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(urlparse(url).path).stem).strip("._-")[:80] or "download"
    digest = hashlib.sha256(raw).hexdigest()[:8]
    target = folder / f"{stem}.pdf"
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()[:8] != digest:
        target = folder / f"{stem}-{digest}.pdf"
    if not target.is_file():
        target.write_bytes(raw)
    return target


def _pdf_text(path):
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return None, "the PDF is encrypted"
        return "\n\n".join(f"[page {i}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, 1)), None
    except Exception as e:
        return None, f"its text could not be read ({type(e).__name__})"


def fetch(url, max_chars=DEFAULT_CHARS, start=0):
    final, r = _get(url)
    if r.status_code != 200:
        r.close()
        raise Refused("url", f"the site answered HTTP {r.status_code}", "Unavailable", 5)
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    raw = _body(r)
    host = urlparse(final).hostname
    out = {"url": url, "final_url": final if final != url else None, "content_type": ctype or None, "bytes": len(raw), "title": None}
    if ctype == "application/pdf" or raw[:5] == b"%PDF-":
        saved = _save_pdf(raw, final, host)
        rel = saved.relative_to(Path(automc_paths.WORKSPACE).resolve()).as_posix()
        text, problem = _pdf_text(saved)
        out.update(saved_to=rel, next=f"its figures need a knowledge base: DocumentParseTool on the folder {Path(rel).parent.as_posix()}")
        if text is None:
            text = ""
            out["note"] = f"saved, but {problem}"
    elif ctype in ("text/html", "application/xhtml+xml") or (not ctype and raw.lstrip()[:1] == b"<"):
        out["title"], text = _html_text(raw, r.encoding)
    elif ctype.startswith("text/") or ctype in TEXT_TYPES:
        text = raw.decode(r.encoding or "utf-8", errors="replace")
    else:
        raise Refused("url", f"the response is {ctype or 'of no stated type'}: not a web page, a PDF or text", "NotAllowed", 3)
    start = max(0, int(start or 0))
    piece = text[start:start + max_chars]
    out.update(chars=len(text), start=start, text=piece, facility_data="public")
    if start + len(piece) < len(text):
        out["next_start"] = start + len(piece)
        out["more"] = f"{len(text) - start - len(piece):,} more characters; call again with start={start + len(piece)}"
    return out
