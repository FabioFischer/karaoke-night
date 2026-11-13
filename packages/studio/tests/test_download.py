import pytest
from studio.download import source_metadata, video_id_from

VIDEO_ID = "XXXXXXXXXXX"


def test_metadata_from_a_youtube_music_track() -> None:
    info = {
        "id": VIDEO_ID,
        "title": "Song Title (Official Audio)",
        "track": "Song Title",
        "artists": ["Artist Name", "Guest Name"],
        "album": "Album Name",
        "duration": 245,
        "uploader": "Artist Name - Topic",
    }

    assert source_metadata(info) == {
        "video_id": VIDEO_ID,
        "title": "Song Title",
        "artist": "Artist Name, Guest Name",
        "album": "Album Name",
        "duration_s": 245,
    }


def test_metadata_falls_back_to_the_title_and_uploader() -> None:
    info = {"id": VIDEO_ID, "title": "Song Title", "uploader": "Artist Name - Topic"}

    assert source_metadata(info) == {
        "video_id": VIDEO_ID,
        "title": "Song Title",
        "artist": "Artist Name",
        "album": None,
        "duration_s": None,
    }


def test_metadata_takes_a_single_artist_field() -> None:
    info = {"id": VIDEO_ID, "artist": "Artist Name", "creator": "Someone Else"}

    metadata = source_metadata(info)

    assert (metadata["title"], metadata["artist"]) == (VIDEO_ID, "Artist Name")


@pytest.mark.parametrize(
    "target",
    [
        VIDEO_ID,
        f"https://music.youtube.com/watch?v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}&list=PLAYLIST",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42",
        f"https://youtube.com/watch?feature=share&v={VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?si=SHARE",
        f"http://www.youtube.com/watch?v={VIDEO_ID}",
    ],
)
def test_video_id_from_a_url_or_bare_id(target: str) -> None:
    assert video_id_from(target) == VIDEO_ID


@pytest.mark.parametrize(
    "target",
    [
        "song.flac",
        "XXXXXXXXXX",
        "XXXXXXXXXXXX",
        "https://example.com/watch?v=XXXXXXXXXXX",
        "https://www.youtube.com/watch?list=PLAYLIST",
        "https://www.youtube.com/watch?v=short",
    ],
)
def test_video_id_from_anything_else_is_none(target: str) -> None:
    assert video_id_from(target) is None
