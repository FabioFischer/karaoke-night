"""YouTube Music audio and metadata through yt-dlp, imported only where it runs."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.studio_api import VIDEO_ID

WATCH_URL = "https://music.youtube.com/watch?v={}"
_WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def video_id_from(target: str) -> str | None:
    """The video_id of a YouTube or YouTube Music watch URL, a youtu.be link or a bare id."""
    if VIDEO_ID.fullmatch(target):
        return target
    url = urlparse(target)
    if url.scheme not in ("http", "https"):
        return None
    if url.hostname == "youtu.be":
        candidate = url.path.lstrip("/")
    elif url.hostname in _WATCH_HOSTS and url.path == "/watch":
        candidate = parse_qs(url.query).get("v", [""])[0]
    else:
        return None
    return candidate if VIDEO_ID.fullmatch(candidate) else None


def source_metadata(info: dict) -> dict:
    """Title, artist, album and duration from a yt-dlp info dict.

    YouTube Music fills track and artists; other uploads fall back to the
    video title and the channel.
    """
    artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
    artist = ", ".join(artists) or info.get("creator") or info.get("uploader") or ""
    return {
        "video_id": info["id"],
        "title": info.get("track") or info.get("title") or info["id"],
        "artist": artist.removesuffix(" - Topic"),
        "album": info.get("album"),
        "duration_s": info.get("duration"),
    }


def fetch_info(video_id: str) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL({"noplaylist": True, "quiet": True}) as ydl:
        return ydl.extract_info(WATCH_URL.format(video_id), download=False)


def download_audio(video_id: str, out_dir: Path) -> Path:
    """The best audio of the video, as yt-dlp gives it, in out_dir."""
    import yt_dlp

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "download.%(ext)s"),
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(WATCH_URL.format(video_id), download=True)
    return Path(info["requested_downloads"][0]["filepath"])
