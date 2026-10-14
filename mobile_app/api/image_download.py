"""Bounded public-image fetcher. Never follow a URL using a second DNS lookup."""
import http.client
import io
import ipaddress
import queue
import socket
import ssl
import threading
import time
import warnings
from urllib.parse import urljoin, urlsplit
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 8 * 1024 * 1024
TOTAL_SECONDS = 20
CONNECT_SECONDS = 3
READ_SECONDS = 5
MAX_REDIRECTS = 3


class ImageDownloadError(ValueError):
    pass


def validate_image(content):
    """Check decoded format and integrity, not an untrusted Content-Type header."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
                    raise ImageDownloadError("Use a JPEG, PNG, GIF, or WebP image")
                if image.width * image.height > 20_000_000:
                    raise ImageDownloadError("Image dimensions are too large")
                image.verify()
            # Some decoders verify only the header. Decode the first frame as well.
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                return image.format
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageDownloadError("Downloaded file is not a valid image") from exc


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ImageDownloadError("Image download deadline exceeded")
    return remaining


def _resolve(host, port, deadline):
    # getaddrinfo itself has no timeout: isolate it without blocking the request past the deadline.
    result = queue.Queue(maxsize=1)
    def lookup():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as exc:
            result.put(exc)
    threading.Thread(target=lookup, daemon=True).start()
    try:
        rows = result.get(timeout=min(CONNECT_SECONDS, _remaining(deadline)))
    except queue.Empty as exc:
        raise ImageDownloadError("Image host resolution timed out") from exc
    if isinstance(rows, Exception):
        raise ImageDownloadError("Could not resolve image host") from rows
    addresses = list(dict.fromkeys(row[4][0] for row in rows))
    def public(address):
        ip = ipaddress.ip_address(address)
        if not ip.is_global or "%" in address:
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            # Do not permit transition mechanisms to turn a public IPv6 address
            # into an internal IPv4 destination.
            if ip.ipv4_mapped or ip.sixtofour or ip.teredo:
                return False
            if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"):
                return False
        return True
    if not addresses or any(not public(address) for address in addresses):
        raise ImageDownloadError("Image URL must resolve only to public addresses")
    return addresses


def _connection(url, deadline):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ImageDownloadError("Use a public HTTP or HTTPS image URL without credentials")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ImageDownloadError("Invalid image URL port") from exc
    if port not in {80, 443}:
        raise ImageDownloadError("Image URLs must use port 80 or 443")
    addresses = _resolve(parts.hostname, port, deadline)
    timeout = min(CONNECT_SECONDS, _remaining(deadline))
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    # Preserve the original host for Host, TLS SNI and certificate verification;
    # connect only to the already validated address, bypassing proxies and DNS rebinding.
    def connect_pinned(address, timeout=None, source_address=None, **kwargs):
        sock = socket.create_connection((addresses[0], port),
            timeout=min(CONNECT_SECONDS, _remaining(deadline)), source_address=source_address)
        try:
            # HTTPSConnection performs TLS after this hook; give that handshake
            # only the remaining budget, not the original pre-connect timeout.
            sock.settimeout(min(CONNECT_SECONDS, _remaining(deadline)))
            return sock
        except Exception:
            sock.close()
            raise
    conn._create_connection = connect_pinned
    return conn, (parts.path or "/") + (("?" + parts.query) if parts.query else "")


def download_image(url):
    deadline = time.monotonic() + TOTAL_SECONDS
    for hop in range(MAX_REDIRECTS + 1):
        conn = None
        response = None
        timer = None
        try:
            conn, path = _connection(url, deadline)
            conn.connect()
            conn.sock.settimeout(min(READ_SECONDS, _remaining(deadline)))
            # Force-close even during slow headers or a drip-fed body. Read timeout alone
            # measures idle periods and cannot enforce a total transfer deadline.
            sock = conn.sock
            def abort():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            timer = threading.Timer(_remaining(deadline), abort)
            timer.daemon = True
            timer.start()
            conn.request("GET", path, headers={"Accept": "image/*", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or hop == MAX_REDIRECTS:
                    raise ImageDownloadError("Too many image redirects or missing redirect target")
                url = urljoin(url, location)
                continue  # The next destination is independently resolved and validated.
            if response.status != 200:
                raise ImageDownloadError("Image host did not return a successful response")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ImageDownloadError("Compressed image transfers are not supported")
            length = response.getheader("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ImageDownloadError("Image is too large (max 8 MB)")
            content = bytearray()
            while True:
                sock.settimeout(min(READ_SECONDS, _remaining(deadline)))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ImageDownloadError("Image is too large (max 8 MB)")
            _remaining(deadline)
            body = bytes(content)
            fmt = validate_image(body)
            _remaining(deadline)
            return body, fmt
        except ImageDownloadError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise ImageDownloadError("Could not download image within the allowed limits") from exc
        finally:
            if timer:
                timer.cancel()
            if response:
                response.close()
            if conn:
                conn.close()
    raise ImageDownloadError("Too many image redirects")
