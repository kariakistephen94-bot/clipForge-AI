"""Source ingestion: validated local uploads, public YouTube URLs and public Drive/Dropbox links (no auth bypass)."""

from __future__ import annotations

import html as html_lib
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from fastapi import UploadFile

from ..paths import sanitize_filename
from .probe import ALLOWED_EXTENSIONS, ALLOWED_MIME, sniff_container

log = logging.getLogger(__name__)

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


class IngestError(ValueError):
    pass


class YouTubeBlockedError(IngestError):
    """YouTube's network-wide "confirm you're not a bot" check refused (or would refuse) the download.

    held_back=True means no request was sent because a wait was already in effect.
    """

    def __init__(self, message: str, held_back: bool = False):
        super().__init__(message)
        self.held_back = held_back


async def save_upload(upload: UploadFile, dest_dir: Path, max_bytes: int) -> tuple[Path, int, str]:
    original = upload.filename or "video.mp4"
    safe = sanitize_filename(original, default="video.mp4")
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestError(f"Unsupported file type '{ext or 'none'}'. Upload MP4, MOV or WebM.")
    ctype = (upload.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if ctype not in ALLOWED_MIME:
        raise IngestError(f"Unsupported content type '{ctype}'. Upload MP4, MOV or WebM video.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"original{ext}"
    size = 0
    try:
        with dest.open("wb") as f:
            while True:
                chunk = await upload.read(8 * 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise IngestError(f"File is larger than the configured limit ({max_bytes // 1024**3} GB).")
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise IngestError("The uploaded file is empty.")
    if sniff_container(dest) is None:
        dest.unlink(missing_ok=True)
        raise IngestError("The file content is not a recognised MP4/MOV/WebM video.")
    return dest, size, original


def validate_youtube_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise IngestError("Only public youtube.com / youtu.be links are supported.")
    if "list=" in (parsed.query or "") and "v=" not in (parsed.query or ""):
        raise IngestError("Playlists are not supported; paste a single video link.")
    return url


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]*m")
_RETRYABLE = re.compile(
    r"connection (reset|aborted|refused)|timed? ?out|incomplete ?read|remote end closed|temporarily unavailable|"
    r"network is unreachable|broken pipe|http error 5\d\d|ssl|eof occurred|got error",
    re.I,
)


def clean_error(text: str) -> str:
    """Strip terminal colour codes and yt-dlp's 'ERROR:' prefix from a message."""
    text = _ANSI.sub("", str(text))
    return re.sub(r"^\s*ERROR:\s*", "", text).strip()


def is_retryable_download_error(text: str) -> bool:
    return bool(_RETRYABLE.search(clean_error(text)))


_STREAM_PROTECTION = re.compile(r"PO ?Token|SABR|missing a URL", re.I)

YOUTUBE_STREAM_REFUSED = (
    "YouTube refused to send this video's stream to ClipForge AI. For this video YouTube now requires a "
    "proof-of-origin (PO) token — a bot-protection measure — and ClipForge AI does not bypass YouTube's protections "
    "(no token generators, no borrowed browser cookies). Get the video file through a method you're permitted to use "
    "(for example the campaign's provided source files, or YouTube Studio if it's your own channel) and upload it instead."
)


# Audio first (small, original language preferred by yt-dlp's sort), then ≤1080p video. When a stream keeps
# stalling we move to the next plan: Opus audio, then segmented HLS audio. Never a dubbed track by choice.
# H.264 video is preferred over AV1/VP9: it decodes far faster on machines without AV1 hardware decoding,
# and every later stage (proxy, framing analysis, rendering) decodes the source again.
_VIDEO_PREF = "bv*[height<=1080][ext=mp4][vcodec^=avc1]"
FORMAT_FALLBACKS = tuple(
    f"{audio}+{_VIDEO_PREF}/{audio}+bv*[height<=1080][ext=mp4]/ba+bv*[height<=1080]/b[height<=1080]/b"
    for audio in ("ba[ext=m4a]", "ba[acodec=opus]", "ba[protocol^=m3u8]")
)


def _dir_bytes(dest_dir: Path) -> int:
    """Bytes of all download artefacts (finished parts and .part files) for this source."""
    return sum(f.stat().st_size for f in dest_dir.glob("original*") if f.is_file())


_BOT_CHECK = re.compile(r"confirm (that )?you.?re not a bot|sign in to confirm", re.I)

YOUTUBE_BOT_CHECK = (
    "YouTube is asking this connection to sign in to prove it isn't a bot, so it won't serve the video to ClipForge AI. "
    "This usually happens after many downloads from the same network and typically clears after a while. "
    "ClipForge AI does not sign in to YouTube or use your browser cookies to get around this check. "
    "Get the video file through a method you're permitted to use (for example the campaign's source files, or "
    "YouTube Studio if it's your own channel) and upload it instead, or try the link again later."
)


def is_youtube_bot_check(text: str) -> bool:
    return bool(_BOT_CHECK.search(clean_error(text)))


def is_youtube_stream_refused(warnings: list[str], bytes_downloaded: int, error: str) -> bool:
    """True when the stream was refused outright because YouTube demands a PO token / SABR streaming."""
    return (
        bytes_downloaded == 0
        and any(_STREAM_PROTECTION.search(w) for w in warnings)
        and is_retryable_download_error(error)
    )


class _YtLogger:
    """Collects yt-dlp messages instead of printing them (used to explain failures)."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, msg: str) -> None:
        if _STREAM_PROTECTION.search(msg):
            self.warnings.append(clean_error(msg))

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(clean_error(msg))

    def error(self, msg: str) -> None:
        log.debug("yt-dlp: %s", clean_error(msg)[:300])


def download_youtube(url: str, dest_dir: Path, on_progress: Callable[[float, str], None] | None = None,
                     max_stalls: int = 6, max_attempts: int = 40,
                     sleep: Callable[[float], None] = time.sleep) -> tuple[Path, dict[str, Any]]:
    """Download a PUBLIC video. No cookies, no login, no restriction bypass.

    Large downloads survive dropped connections: yt-dlp retries internally, downloads in chunks,
    and partial files are resumed on the next attempt instead of starting over.
    """
    import yt_dlp

    url = validate_youtube_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ytlog = _YtLogger()
    base_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        # YouTube's SABR / PO-token notices are only emitted as debug lines; they go to our logger, not the console.
        "verbose": True,
        "logger": ytlog,
        "noplaylist": True,
        "cookiefile": None,
        "noprogress": True,
        "color": "no_color",
        "socket_timeout": 30,
        "extractor_retries": 3,
    }
    try:
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        if is_youtube_bot_check(str(e)):
            raise YouTubeBlockedError(YOUTUBE_BOT_CHECK) from e
        raise IngestError(
            "This video isn't publicly accessible (private, age/region restricted, members-only, or removed). "
            "ClipForge AI does not bypass access restrictions -- upload a file you have permission to use instead."
        ) from e
    availability = (info or {}).get("availability")
    if availability not in (None, "public", "unlisted"):
        raise IngestError(f"Video availability is '{availability}'. Only public videos can be fetched by URL.")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise IngestError("Live streams can't be processed. Wait until the stream is archived.")
    if (info.get("duration") or 0) > 6 * 3600:
        raise IngestError("Videos longer than 6 hours are not supported.")

    # Separate video+audio streams are downloaded one after another; report overall progress across them.
    state: dict[str, Any] = {"stream": 0, "last_file": "", "bytes": 0}
    n_streams = max(1, len(info.get("requested_formats") or []) or 2)

    def hook(d: dict[str, Any]) -> None:
        if not on_progress or d.get("status") != "downloading":
            return
        fname = str(d.get("filename", ""))
        if fname != state["last_file"]:
            if state["last_file"]:
                state["stream"] = min(n_streams - 1, int(state["stream"]) + 1)
            state["last_file"] = fname
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes", 0) or 0
        state["bytes"] = max(int(state["bytes"]), int(done))
        if total:
            frac = (int(state["stream"]) + min(1.0, done / total)) / n_streams
            speed = d.get("speed") or 0
            detail = f"Downloading stream {int(state['stream']) + 1}/{n_streams}: {done / 1024**2:.0f}/{total / 1024**2:.0f} MB"
            if speed:
                detail += f" at {speed / 1024**2:.1f} MB/s"
            on_progress(frac, detail)

    opts = {
        **base_opts,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "original.%(ext)s"),
        "progress_hooks": [hook],
        "overwrites": False,  # keep .part files so a retry resumes instead of restarting
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "http_chunk_size": 10 * 1024 * 1024,  # chunked requests survive resets/throttling on large files
        # Short backoff: mid-download drops recover quickly, and a stream that is refused outright fails in ~1-2 min.
        "retry_sleep_functions": {"http": lambda n: min(10, 2 ** n), "fragment": lambda n: min(10, 2 ** n)},
    }
    # Progress-aware retries: an attempt that received data doesn't count as a stall, so long downloads over
    # a flaky connection keep resuming; we only give up after `max_stalls` consecutive attempts with no new data.
    last_error = ""
    fmt_idx = 0
    stalls = 0
    attempt = 0
    while True:
        attempt += 1
        opts["format"] = FORMAT_FALLBACKS[fmt_idx]
        before = _dir_bytes(dest_dir)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            break
        except yt_dlp.utils.DownloadError as e:
            last_error = clean_error(str(e))
            if is_youtube_bot_check(last_error):
                log.warning("YouTube bot check triggered; not retrying (no cookies/sign-in by design)")
                raise YouTubeBlockedError(YOUTUBE_BOT_CHECK) from e
            after = _dir_bytes(dest_dir)
            progressed = after > before + 256 * 1024
            stalls = 0 if progressed else stalls + 1
            if not is_retryable_download_error(last_error) or stalls >= max_stalls or attempt >= max_attempts:
                if is_youtube_stream_refused(ytlog.warnings, after, last_error):
                    log.warning("YouTube refused the stream (PO token / SABR): %s", "; ".join(ytlog.warnings)[:300])
                    raise IngestError(YOUTUBE_STREAM_REFUSED) from e
                raise IngestError(
                    f"Download failed after {attempt} attempt(s): {last_error[:220]}. "
                    f"{after / 1024**2:.0f} MB are kept, so clicking Analyze again resumes the download. "
                    "If it keeps failing, get the video file another permitted way and upload it."
                ) from e
            note = ""
            if stalls >= 2 and stalls % 2 == 0 and fmt_idx < len(FORMAT_FALLBACKS) - 1:
                fmt_idx += 1
                note = ", trying an alternative stream format"
            wait = 3 if progressed else min(60, 5 * 2 ** (stalls - 1))
            log.warning("YouTube download interrupted after %.0f MB (%s); resuming in %ss%s",
                        after / 1024**2, last_error[:120], wait, note)
            if on_progress:
                on_progress(-1.0, f"Connection dropped at {after / 1024**2:.0f} MB; resuming in {wait}s{note}")
            sleep(wait)
    files = sorted((f for f in dest_dir.glob("original.*") if f.stem == "original"),
                   key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if f.suffix.lower() in ALLOWED_EXTENSIONS]
    if not files:
        raise IngestError("Download finished but no video file was produced.")
    return files[0], {"title": info.get("title"), "channel": info.get("channel"), "duration": info.get("duration"),
                      "webpage_url": info.get("webpage_url")}


# --------------------------------------------------------------------------- shared links (Google Drive / Dropbox)

DRIVE_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}
DROPBOX_HOSTS = {"dropbox.com", "www.dropbox.com", "dl.dropbox.com", "dl.dropboxusercontent.com"}
LINK_LABELS = {"youtube": "YouTube video", "drive": "Google Drive file", "dropbox": "Dropbox file"}
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def link_origin(url: str) -> str:
    """Classify a pasted link as 'youtube', 'drive' or 'dropbox'."""
    host = (urlparse((url or "").strip()).hostname or "").lower()
    if host in YOUTUBE_HOSTS:
        return "youtube"
    if host in DRIVE_HOSTS:
        return "drive"
    if host in DROPBOX_HOSTS:
        return "dropbox"
    raise IngestError("Paste a YouTube, Google Drive or Dropbox link.")


def validate_source_link(url: str) -> tuple[str, str]:
    """Return (origin, normalized url) for a YouTube, Google Drive or Dropbox link."""
    url = (url or "").strip()
    origin = link_origin(url)
    if origin != "youtube" and urlparse(url).scheme != "https":
        raise IngestError("Google Drive and Dropbox links must start with https://")
    if origin == "youtube":
        return origin, validate_youtube_url(url)
    share_download_url(url)  # raises for folders and malformed links
    return origin, url


def share_download_url(url: str) -> str:
    """Turn a Drive/Dropbox share link into its direct-download URL (only works for 'anyone with the link')."""
    parsed = urlparse(url)
    origin = link_origin(url)
    if origin == "drive":
        if drive_folder_id(url):
            raise IngestError("That's a Google Drive folder. Pick the video(s) from the folder list instead.")
        m = re.search(r"/(?:file/)?d/([^/?#]+)", parsed.path)
        query = parse_qs(parsed.query)
        file_id = m.group(1) if m else query.get("id", [""])[0]
        if not _DRIVE_ID.match(file_id or ""):
            raise IngestError("Couldn't find the file in that Google Drive link. Use the file's Share → Copy link.")
        # confirm=t skips the "can't scan this large file for viruses" page Drive shows for big files.
        direct = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
        resource_key = query.get("resourcekey", [""])[0]  # older shared links need it to download
        if re.fullmatch(r"[A-Za-z0-9_-]{4,}", resource_key):
            direct += f"&resourcekey={resource_key}"
        return direct
    if origin == "dropbox":
        if parsed.path.startswith(("/sh/", "/scl/fo/")):
            raise IngestError("That's a Dropbox folder. Open the video inside it and copy that file's link.")
        if not parsed.path.startswith(("/s/", "/scl/fi/")) and parsed.hostname != "dl.dropboxusercontent.com":
            raise IngestError("Couldn't find the file in that Dropbox link. Use the file's Share → Copy link.")
        query = {k: v for k, v in parse_qs(parsed.query).items() if k != "dl"}
        query["dl"] = ["1"]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    raise IngestError("Only Google Drive and Dropbox share links can be downloaded this way.")


def drive_folder_id(url: str) -> str | None:
    """The folder id of a Google Drive folder link, or None if the link isn't a folder."""
    parsed = urlparse((url or "").strip())
    if (parsed.hostname or "").lower() not in DRIVE_HOSTS:
        return None
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", parsed.path)
    if m:
        return m.group(1)
    if parsed.path.rstrip("/") in ("/embeddedfolderview", "/drive/folderview"):
        fid = parse_qs(parsed.query).get("id", [""])[0]
        return fid if _DRIVE_ID.match(fid) else None
    return None


_NON_VIDEO_EXT = {
    ".txt", ".pdf", ".doc", ".docx", ".rtf", ".md", ".csv", ".xls", ".xlsx", ".ppt", ".pptx", ".key", ".pages",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".svg", ".psd", ".ai", ".zip", ".rar", ".7z",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".srt", ".vtt", ".ttf", ".otf", ".json", ".xml",
}
_FOLDER_ENTRY = re.compile(r'<div class="flip-entry" id="entry-([A-Za-z0-9_-]+)"(.*?)</a>', re.S)


def _drive_video_name(name: str) -> bool:
    ext = Path(name).suffix.lower()
    return ext in ALLOWED_EXTENSIONS or ext not in _NON_VIDEO_EXT and not re.fullmatch(r"\.[a-z0-9]{1,5}", ext)


def list_drive_folder(url: str, *, max_depth: int = 2, max_files: int = 300, max_folders: int = 60,
                      opener: Callable[..., Any] = urllib.request.urlopen) -> list[dict[str, str]]:
    """List the videos in a publicly shared Google Drive folder (and its subfolders, up to max_depth levels).

    Uses Drive's public embedded folder view, so it needs no sign-in or API key; only folders shared with
    "Anyone with the link" can be listed. Returns [{id, name, url, folder}] sorted by folder, then name.
    """
    root = drive_folder_id(url)
    if not root:
        raise IngestError("That isn't a Google Drive folder link.")
    not_shared = "Google Drive didn't show that folder. Set its sharing to \"Anyone with the link\" and try again."

    def fetch(folder_id: str) -> str | None:
        req = urllib.request.Request(f"https://drive.google.com/embeddedfolderview?id={folder_id}",
                                     headers={"User-Agent": "Mozilla/5.0 ClipForgeAI/0.1"})
        try:
            with opener(req, timeout=30) as resp:
                return str(resp.read(4 * 1024 * 1024).decode("utf-8", "replace"))
        except urllib.error.HTTPError:
            return None
        except OSError as e:
            raise IngestError(f"Couldn't reach Google Drive ({clean_error(str(e))[:120]}).") from e

    def parse(page: str, path: str) -> list[tuple[str, str]]:
        subfolders: list[tuple[str, str]] = []
        for entry_id, body in _FOLDER_ENTRY.findall(page):
            title = re.search(r'class="flip-entry-title">([^<]*)', body)
            name = html_lib.unescape(title.group(1)).strip() if title else entry_id
            if "/folders/" in body:
                subfolders.append((entry_id, f"{path}/{name}" if path else name))
            elif "/file/d/" in body and _drive_video_name(name) and len(files) < max_files:  # skips native Docs
                files.append({"id": entry_id, "name": name, "folder": path,
                              "url": f"https://drive.google.com/file/d/{entry_id}/view"})
        return subfolders

    files: list[dict[str, str]] = []
    page = fetch(root)
    if page is None or ("flip-entry" not in page and "flip-list" not in page and "folder-view" not in page):
        raise IngestError(not_shared)
    level = parse(page, "")
    seen = {root}
    # Subfolders are read level by level, several at a time; at most max_folders are opened.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _depth in range(max_depth):
            level = [(fid, path) for fid, path in level if fid not in seen][: max(0, max_folders - len(seen))]
            if not level or len(files) >= max_files:
                break
            seen.update(fid for fid, _ in level)
            pages = list(pool.map(lambda item: fetch(item[0]), level))
            nxt: list[tuple[str, str]] = []
            for (_fid, path), sub in zip(level, pages, strict=True):
                if sub is not None:
                    nxt += parse(sub, path)
            level = nxt
    files.sort(key=lambda f: (f["folder"].lower(), f["name"].lower()))
    return files


_DRIVE_QUOTA = re.compile(r"quota|too many users have (viewed|downloaded)", re.I)
_DRIVE_QUOTA_MSG = ("Google Drive has hit its download limit for this file (too many people downloaded it today). "
                    "The limit resets within 24 hours, or ask the campaign for another link.")


def _drive_interstitial(page: str) -> str | None:
    """Follow Drive's "can't scan this file for viruses" page: return the real download URL, or None."""
    form = re.search(r'<form[^>]*id="download-form"[^>]*action="([^"]+)"[^>]*>(.*?)</form>', page, re.S)
    if not form:
        return None
    action = html_lib.unescape(form.group(1))
    if (urlparse(action).hostname or "").lower() not in DRIVE_HOSTS:
        return None
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form.group(2)))
    if not fields.get("id"):
        return None
    return f"{action}?{urlencode({k: html_lib.unescape(v) for k, v in fields.items()})}"


_NOT_SHARED = {
    "drive": ("Google Drive didn't send the video. Set the file's sharing to \"Anyone with the link\" and try again. "
              "If it is already shared that way, Drive may have hit its download limit for this file; that resets within 24 hours."),
    "dropbox": ("Dropbox didn't send the video. Check the link still works and that it's shared with anyone who has the link."),
}


def _filename_from_headers(headers: Any) -> str | None:
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    return unquote(m.group(1)).strip() if m else None


def download_shared_file(url: str, dest_dir: Path, max_bytes: int,
                         on_progress: Callable[[float, str], None] | None = None,
                         max_stalls: int = 6, sleep: Callable[[float], None] = time.sleep,
                         opener: Callable[..., Any] = urllib.request.urlopen) -> tuple[Path, str]:
    """Download a publicly shared Drive/Dropbox video. Returns (path, original filename).

    Partial data is kept in original.part and resumed with HTTP Range requests after a dropped connection.
    """
    origin = link_origin(url)
    direct = share_download_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    part = dest_dir / "original.part"
    filename: str | None = None
    total = 0
    stalls = 0
    hops = 0
    too_big = f"File is larger than the configured limit ({max_bytes // 1024**3} GB)."
    while True:
        before = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(direct, headers={"User-Agent": "ClipForgeAI/0.1"})
        if before:
            req.add_header("Range", f"bytes={before}-")
        try:
            with opener(req, timeout=60) as resp:
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype.startswith("text/"):
                    page = resp.read(1024 * 1024).decode("utf-8", "replace") if origin == "drive" else ""
                    nxt = _drive_interstitial(page) if page else None
                    if nxt and hops < 3 and not before:
                        hops += 1
                        direct = nxt
                        continue
                    if _DRIVE_QUOTA.search(page):
                        raise IngestError(_DRIVE_QUOTA_MSG)
                    raise IngestError(_NOT_SHARED[origin])
                filename = filename or _filename_from_headers(resp.headers)
                have = before if resp.status == 206 else 0  # a server that ignores Range restarts from zero
                length = int(resp.headers.get("Content-Length") or 0)
                total = have + length if length else total
                if total > max_bytes:
                    raise IngestError(too_big)
                with part.open("ab" if have else "wb") as f:
                    while chunk := resp.read(1024 * 1024):
                        have += len(chunk)
                        if have > max_bytes:
                            raise IngestError(too_big)
                        f.write(chunk)
                        if on_progress:
                            detail = f"Downloading from {LINK_LABELS[origin].rsplit(' ', 1)[0]}: {have / 1024**2:.0f}"
                            detail += f"/{total / 1024**2:.0f} MB" if total else " MB"
                            on_progress(have / total if total else -1.0, detail)
                if total and have < total:
                    raise ConnectionError(f"connection closed at {have} of {total} bytes")
            break
        except IngestError:
            part.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                part.unlink(missing_ok=True)
                raise IngestError(_NOT_SHARED[origin]) from e
            if e.code == 416 and before:  # range starts at the end: the file was already complete
                break
            err: Exception = e
        except OSError as e:  # includes URLError, timeouts and ConnectionError
            err = e
        got = part.stat().st_size if part.exists() else 0
        stalls = 0 if got > before + 256 * 1024 else stalls + 1
        if stalls >= max_stalls:
            raise IngestError(
                f"Download kept failing ({clean_error(str(err))[:160]}). {got / 1024**2:.0f} MB are kept, "
                "so clicking Analyze again resumes the download."
            ) from err
        wait = 3 if stalls == 0 else min(60, 5 * 2 ** (stalls - 1))
        log.warning("Shared-link download interrupted at %.0f MB (%s); resuming in %ss", got / 1024**2, err, wait)
        if on_progress:
            on_progress(-1.0, f"Connection dropped at {got / 1024**2:.0f} MB; resuming in {wait}s")
        sleep(wait)

    if not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise IngestError("The shared file is empty.")
    kind = sniff_container(part)
    if kind is None:
        part.unlink(missing_ok=True)
        raise IngestError("The shared file isn't an MP4, MOV or WebM video.")
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".webm" if kind == "webm" else ".mp4"
    final = dest_dir / f"original{ext}"
    part.replace(final)
    return final, filename or LINK_LABELS[origin]
