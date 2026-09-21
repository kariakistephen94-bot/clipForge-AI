import io
import urllib.error
from email.message import Message
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.ingest import (
    YOUTUBE_BOT_CHECK,
    IngestError,
    _YtLogger,
    clean_error,
    download_shared_file,
    drive_folder_id,
    is_retryable_download_error,
    is_youtube_bot_check,
    is_youtube_stream_refused,
    list_drive_folder,
    share_download_url,
    validate_source_link,
    validate_youtube_url,
)

SABR_WARNING = ("[youtube] xGjSGfRGFHI: Some web client https formats have been skipped as they are missing a URL. "
                "YouTube is forcing SABR streaming for this client.")


BOT_ERROR = ("\x1b[0;31mERROR:\x1b[0m [youtube] xGjSGfRGFHI: Sign in to confirm you’re not a bot. "
             "Use --cookies-from-browser or --cookies for the authentication.")


def test_bot_check_detection_and_message():
    assert is_youtube_bot_check(BOT_ERROR)
    assert is_youtube_bot_check("Sign in to confirm you're not a bot")
    assert not is_youtube_bot_check(REAL_ERROR)
    assert not is_youtube_bot_check("Video unavailable")
    # the user-facing message must not tell people to hand over browser cookies
    assert "--cookies" not in YOUTUBE_BOT_CHECK and "upload" in YOUTUBE_BOT_CHECK


def test_stream_refused_detection():
    assert is_youtube_stream_refused([SABR_WARNING], 0, REAL_ERROR)
    # a mid-download drop after data arrived is a normal retryable failure, not a refusal
    assert not is_youtube_stream_refused([SABR_WARNING], 50_000_000, REAL_ERROR)
    # resets without any protection warning are treated as ordinary network trouble
    assert not is_youtube_stream_refused([], 0, REAL_ERROR)
    assert not is_youtube_stream_refused([SABR_WARNING], 0, "Video unavailable")


def test_yt_logger_keeps_only_relevant_debug_lines():
    lg = _YtLogger()
    lg.debug("[youtube] Extracting URL")
    lg.debug("[debug] [youtube] [pot] PO Token Providers: none")
    lg.warning("\x1b[0;33mWARNING:\x1b[0m " + SABR_WARNING)
    assert len(lg.warnings) == 2 and all("\x1b" not in w for w in lg.warnings)

REAL_ERROR = "\x1b[0;31mERROR:\x1b[0m [download] Got error: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))"


def test_clean_error_strips_ansi_and_prefix():
    assert clean_error(REAL_ERROR).startswith("[download] Got error:")
    assert clean_error("[0;31mERROR:[0m boom") == "boom"


@pytest.mark.parametrize("msg,retry", [
    (REAL_ERROR, True),
    ("HTTP Error 503: Service Unavailable", True),
    ("The read operation timed out", True),
    ("IncompleteRead(1234 bytes read)", True),
    ("Video unavailable. This video is private", False),
    ("Sign in to confirm your age", False),
])
def test_retryable_classification(msg, retry):
    assert is_retryable_download_error(msg) is retry


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=xGjSGfRGFHI", "https://youtu.be/xGjSGfRGFHI"])
def test_valid_youtube_urls(url):
    assert validate_youtube_url(url) == url


@pytest.mark.parametrize("url", ["https://evil.com/watch?v=1", "file:///etc/passwd", "https://www.youtube.com/playlist?list=PL1", ""])
def test_invalid_youtube_urls(url):
    with pytest.raises(IngestError):
        validate_youtube_url(url)


# ---------------------------------------------------------------- Google Drive / Dropbox links

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12


def test_share_links_are_classified_and_converted():
    assert validate_source_link("https://youtu.be/abcdefghijk")[0] == "youtube"
    for url in ("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view?usp=sharing",
                "https://drive.google.com/open?id=1AbCdEfGhIjKlMn",
                "https://drive.google.com/uc?id=1AbCdEfGhIjKlMn&export=download"):
        assert validate_source_link(url)[0] == "drive"
        assert share_download_url(url) == (
            "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMn&export=download&confirm=t")
    db = share_download_url("https://www.dropbox.com/scl/fi/abc123/talk.mp4?rlkey=xyz&dl=0")
    assert "rlkey=xyz" in db and "dl=1" in db and "dl=0" not in db
    assert validate_source_link("https://www.dropbox.com/s/abc123/talk.mp4?dl=0")[0] == "dropbox"


@pytest.mark.parametrize("url", [
    "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn",
    "https://www.dropbox.com/scl/fo/abc/def?rlkey=x",
    "https://drive.google.com/file/d/x/view",
    "http://drive.google.com/file/d/1AbCdEfGhIjKlMn/view",
    "https://vimeo.com/12345",
])
def test_bad_share_links_rejected(url):
    with pytest.raises(IngestError):
        validate_source_link(url)


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, ctype: str = "video/mp4", length: int | None = None,
                 name: str | None = None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = ctype
        self.headers["Content-Length"] = str(len(body) if length is None else length)
        if name:
            self.headers["Content-Disposition"] = f"attachment; filename=\"{name}\""


class _FakeServer:
    """Serves `data`; the first response is cut off after `cut` bytes to simulate a dropped connection."""

    def __init__(self, data: bytes, cut: int | None = None, **kw):
        self.data, self.cut, self.kw, self.ranges = data, cut, kw, []

    def __call__(self, req, timeout=None):
        rng = req.get_header("Range")
        self.ranges.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        body = self.data[start:]
        if self.cut is not None:
            cut, self.cut = self.cut, None
            return _Resp(body[:cut], 206 if rng else 200, length=len(body), **self.kw)
        return _Resp(body, 206 if rng else 200, **self.kw)


def test_shared_download_resumes_after_drop(tmp_path):
    data = MP4_HEAD + bytes(range(256)) * 4000
    server = _FakeServer(data, cut=300_000, name="Episode 12.mov")
    progress = []
    path, name = download_shared_file("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view", tmp_path, 10**9,
                                      on_progress=lambda f, d: progress.append(f), sleep=lambda s: None,
                                      opener=server)
    assert path.name == "original.mov" and name == "Episode 12.mov"
    assert path.read_bytes() == data
    assert server.ranges == [None, "bytes=300000-"]
    assert not (tmp_path / "original.part").exists()
    assert progress[-1] == pytest.approx(1.0)


def test_shared_download_explains_unshared_file(tmp_path):
    html = _FakeServer(b"<html>Sign in</html>", ctype="text/html")
    with pytest.raises(IngestError, match="Anyone with the link"):
        download_shared_file("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view", tmp_path, 10**9, opener=html)

    def forbidden(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", Message(), None)
    with pytest.raises(IngestError, match="shared with anyone"):
        download_shared_file("https://www.dropbox.com/s/abc/x.mp4", tmp_path, 10**9, opener=forbidden)
    assert not list(tmp_path.iterdir())


def test_shared_download_rejects_non_video_and_oversize(tmp_path):
    with pytest.raises(IngestError, match="isn't an MP4"):
        download_shared_file("https://www.dropbox.com/s/abc/x.mp4", tmp_path, 10**9,
                             opener=_FakeServer(b"PK\x03\x04 not a video" * 10, ctype="application/octet-stream"))
    with pytest.raises(IngestError, match="larger than"):
        download_shared_file("https://www.dropbox.com/s/abc/x.mp4", tmp_path, 100, opener=_FakeServer(MP4_HEAD * 10))
    assert not list(tmp_path.iterdir())


def test_shared_download_gives_up_after_repeated_stalls(tmp_path):
    def down(req, timeout=None):
        raise urllib.error.URLError("Network is unreachable")
    with pytest.raises(IngestError, match="kept failing"):
        download_shared_file("https://www.dropbox.com/s/abc/x.mp4", tmp_path, 10**9, max_stalls=3,
                             sleep=lambda s: None, opener=down)


# ---------------------------------------------------------------- Google Drive folders / interstitials


def _folder_page(entries: list[tuple[str, str, str]]) -> bytes:
    rows = "".join(
        f'<div class="flip-entry" id="entry-{fid}" tabindex="0" role="link"><div class="flip-entry-info">'
        f'<a href="{href}" target="_blank"><div class="flip-entry-title">{name}</div></a></div></div>'
        for fid, name, href in entries)
    return f'<html><body><div class="flip-list">{rows}</div></body></html>'.encode()


def test_drive_folder_links_are_detected():
    assert drive_folder_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn?usp=sharing") == "1AbCdEfGhIjKlMn"
    assert drive_folder_id("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMn") == "1AbCdEfGhIjKlMn"
    assert drive_folder_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view") is None
    assert drive_folder_id("https://www.dropbox.com/scl/fo/abc/def") is None


def test_list_drive_folder_finds_videos_in_subfolders():
    root, sub = "1RootFolder00", "1SubFolder000"
    pages = {
        root: _folder_page([
            ("1VideoAAAAAA", "Episode 1.mp4", "https://drive.google.com/file/d/1VideoAAAAAA/view?usp=drive_web"),
            ("1BriefBBBBBB", "Campaign brief.pdf", "https://drive.google.com/file/d/1BriefBBBBBB/view?usp=drive_web"),
            ("1SlidesCCCCC", "Deck", "https://docs.google.com/presentation/d/1SlidesCCCCC/edit"),
            (sub, "Raw &amp; uncut", f"https://drive.google.com/drive/folders/{sub}"),
        ]),
        sub: _folder_page([("1VideoDDDDDD", "cam A", "https://drive.google.com/file/d/1VideoDDDDDD/view")]),
    }

    def opener(req, timeout=None):
        return _Resp(pages[parse_qs(urlparse(req.full_url).query)["id"][0]], ctype="text/html")

    files = list_drive_folder(f"https://drive.google.com/drive/folders/{root}", opener=opener)
    assert [(f["name"], f["folder"]) for f in files] == [("Episode 1.mp4", ""), ("cam A", "Raw & uncut")]
    assert files[0]["url"] == "https://drive.google.com/file/d/1VideoAAAAAA/view"


def test_list_drive_folder_explains_private_folder():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", Message(), None)
    with pytest.raises(IngestError, match="Anyone with the link"):
        list_drive_folder("https://drive.google.com/drive/folders/1RootFolder00", opener=opener)


def test_drive_virus_scan_page_is_followed(tmp_path):
    data = MP4_HEAD + b"\x01" * 5000
    warning = (b'<html><title>Google Drive - Virus scan warning</title>'
               b'<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
               b'<input type="hidden" name="id" value="1AbCdEfGhIjKlMn"><input type="hidden" name="confirm" value="t">'
               b'<input type="hidden" name="uuid" value="u-123"></form></html>')
    seen = []

    def opener(req, timeout=None):
        seen.append(req.full_url)
        if "uuid=u-123" in req.full_url:
            return _Resp(data, name="big talk.mp4")
        return _Resp(warning, ctype="text/html")

    path, name = download_shared_file("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view?resourcekey=0-Key_1",
                                      tmp_path, 10**9, opener=opener)
    assert path.read_bytes() == data and name == "big talk.mp4"
    assert "resourcekey=0-Key_1" in seen[0] and "uuid=u-123" in seen[1]


def test_drive_quota_page_gets_its_own_message(tmp_path):
    page = _FakeServer(b"<html>Too many users have viewed or downloaded this file recently.</html>", ctype="text/html")
    with pytest.raises(IngestError, match="download limit"):
        download_shared_file("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view", tmp_path, 10**9, opener=page)
