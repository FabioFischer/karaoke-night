import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from studio import cli
from studio.package import build_atomically, song_id

TOKEN = "test-token"
VIDEO_ID = "XXXXXXXXXXX"
# The time as logging's default asctime writes it.
TIME = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}"


def log_line(level: str, name: str, message: str) -> re.Pattern:
    """A whole stderr line with time, level, logger name and message, a regex."""
    return re.compile(f"^{TIME} {level} {re.escape(name)}: {message}$", re.MULTILINE)


class Server:
    """Stands in for uvicorn: records how it was started and uses the app once."""

    def __init__(self, use=None) -> None:
        self.started: list[tuple[str, int]] = []
        self.use = use
        self.result = None

    def __call__(self, app, host: str, port: int) -> None:
        self.started.append((host, port))
        if self.use is not None:
            self.result = self.use(TestClient(app))


def serve(fake_tools, server: Server, *args: str) -> int:
    tools = fake_tools.tools()
    tools.serve = server
    return cli.main(["serve", *args], tools=tools)


@pytest.fixture
def token(monkeypatch) -> str:
    monkeypatch.setenv("STUDIO_TOKEN", TOKEN)
    return TOKEN


def test_serve_starts_on_the_default_host_and_port(
    fake_tools, token, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    server = Server()

    assert serve(fake_tools, server) == 0

    assert server.started == [("0.0.0.0", 8790)]


def test_host_and_port_options(fake_tools, token, tmp_path: Path) -> None:
    server = Server()

    serve(
        fake_tools,
        server,
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--library",
        str(tmp_path),
    )

    assert server.started == [("127.0.0.1", 9000)]


def test_served_app_needs_the_token_from_the_environment(
    fake_tools, token, tmp_path: Path
) -> None:
    def use(client: TestClient):
        return (
            client.get("/songs").status_code,
            client.get("/songs", headers={"Authorization": f"Bearer {TOKEN}"}),
        )

    server = Server(use)
    serve(fake_tools, server, "--library", str(tmp_path))

    without, with_token = server.result
    assert without == 401
    assert (with_token.status_code, with_token.json()) == (200, [])


def test_served_jobs_run_on_the_worker_into_the_library(
    fake_tools, token, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    models = tmp_path / "models"
    built = threading.Event()
    convert = fake_tools.convert

    def convert_then_signal(source, target):
        convert(source, target)
        built.set()

    fake_tools.convert = convert_then_signal

    def use(client: TestClient):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        body = {
            "source": {"type": "youtube_music", "video_id": VIDEO_ID},
            "title": "Song Title",
            "artist": "Artist Name",
        }
        job_id = client.post("/jobs", json=body, headers=headers).json()["job_id"]
        assert built.wait(timeout=10)
        for _ in range(500):
            job = client.get(f"/jobs/{job_id}", headers=headers).json()
            if job["status"] in ("done", "failed"):
                return job
            threading.Event().wait(0.02)
        return job

    server = Server(use)
    serve(fake_tools, server, "--library", str(library), "--model-dir", str(models))

    assert server.result["status"] == "done"
    assert ("make_runner", models) in fake_tools.calls
    (package,) = library.iterdir()
    assert (package / "manifest.json").is_file()


def test_model_dir_and_library_default_like_process() -> None:
    parser, _ = cli.build_parser()

    args = parser.parse_args(["serve"])

    assert args.library == Path("library")
    assert args.model_dir == Path.home() / ".cache" / "audio-separator-models"


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_serve_refuses_to_start_without_the_token(
    fake_tools, monkeypatch, capsys, value
) -> None:
    if value is None:
        monkeypatch.delenv("STUDIO_TOKEN", raising=False)
    else:
        monkeypatch.setenv("STUDIO_TOKEN", value)
    server = Server()

    assert serve(fake_tools, server) == 1

    assert server.started == []
    assert "STUDIO_TOKEN" in capsys.readouterr().err


def test_serve_refuses_to_start_without_the_separation_extra(
    fake_tools, token, capsys
) -> None:
    fake_tools.find_spec = lambda name: None
    server = Server()

    assert serve(fake_tools, server) == 1

    assert server.started == []
    err = capsys.readouterr().err
    assert "studio serve needs the separation extra" in err
    assert "uv sync --package studio --extra separation" in err


def test_serve_help_lists_only_the_planned_options(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["serve", "--help"])

    options = set(re.findall(r"--[a-z-]+", capsys.readouterr().out))
    assert options == {"--help", "--library", "--model-dir", "--host", "--port"}


def test_serve_removes_leftover_builds_and_deletions_before_serving(
    fake_tools, token, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    leftovers = [
        ".song-title--artist-name--a1b2c3.k3j_2h1q.tmp",
        ".song-title--artist-name--d4e5f6.0a1b2c3d.deleting",
    ]
    kept = [
        "song-title--artist-name--a1b2c3",
        ".tmp",
        ".deleting",
        ".settings.tmp",
        "plain.name.tmp",
        ".keep",
    ]
    for name in leftovers + kept:
        (library / name).mkdir(parents=True)
        (library / name / "manifest.json").write_text("{}")
    (library / ".notes.abc.tmp").write_text("a file, not a folder")
    kept.append(".notes.abc.tmp")

    server = Server(lambda client: sorted(p.name for p in library.iterdir()))
    serve(fake_tools, server, "--library", str(library))

    assert server.result == sorted(kept)


def test_serve_starts_on_a_library_that_does_not_exist_yet(
    fake_tools, token, tmp_path: Path
) -> None:
    server = Server()

    assert serve(fake_tools, server, "--library", str(tmp_path / "new")) == 0

    assert server.started == [("0.0.0.0", 8790)]


def test_serve_logs_each_leftover_it_removes_with_level_and_time(
    fake_tools, token, tmp_path: Path, capsys
) -> None:
    library = tmp_path / "library"
    names = []

    def build(out: Path) -> None:
        names.append(out.name)
        raise RuntimeError("stopped mid build")

    with pytest.raises(RuntimeError):
        build_atomically(library, song_id("Song Title", "Artist Name", VIDEO_ID), build)
    (library / names[0]).mkdir()

    serve(fake_tools, Server(), "--library", str(library))

    message = re.escape(f"removed the leftover folder {names[0]}")
    assert log_line("INFO", "studio.library", message).search(capsys.readouterr().err)


def test_serve_logs_an_invalid_manifest_with_level_and_time(
    fake_tools, token, tmp_path: Path, capsys
) -> None:
    library = tmp_path / "library"
    (library / "bad-json").mkdir(parents=True)
    (library / "bad-json" / "manifest.json").write_bytes(b"{not json")
    headers = {"Authorization": f"Bearer {TOKEN}"}

    server = Server(lambda client: client.get("/songs", headers=headers).json())
    serve(fake_tools, server, "--library", str(library))

    assert server.result == []
    message = re.escape("bad-json is not listed: invalid manifest.json (") + r".+\)"
    assert log_line("WARNING", "studio.library", message).search(
        capsys.readouterr().err
    )
