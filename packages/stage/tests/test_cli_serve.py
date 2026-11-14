from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stage import cli

SONG = "song-title--artist-name--a1b2c3"


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


@pytest.fixture
def server(monkeypatch) -> Server:
    server = Server()
    monkeypatch.setattr(cli, "run_server", server)
    return server


def test_serve_binds_the_loopback_on_port_8791(server, tmp_path: Path) -> None:
    assert cli.main(["serve", "--library", str(tmp_path)]) == 0

    assert server.started == [("127.0.0.1", 8791)]


def test_port_option(server, tmp_path: Path) -> None:
    cli.main(["serve", "--library", str(tmp_path), "--port", "9000"])

    assert server.started == [("127.0.0.1", 9000)]


def test_serve_without_studio_or_token_says_so_and_serves_the_library(
    server, packages, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("STUDIO_TOKEN", raising=False)
    packages.add(SONG)
    server.use = lambda client: client.get("/api/library").json()

    assert cli.main(["serve", "--library", str(packages.path)]) == 0

    assert [song["song_id"] for song in server.result["songs"]] == [SONG]
    err = capsys.readouterr().err
    assert (
        "stage serve: no Studio (--studio and the STUDIO_TOKEN environment "
        "variable); only songs in the library can be added"
    ) in err


def test_serve_with_studio_and_token_does_not_warn(
    server, monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setenv("STUDIO_TOKEN", "test-token")

    cli.main(
        ["serve", "--library", str(tmp_path), "--studio", "http://studio.invalid:8790"]
    )

    assert "no Studio" not in capsys.readouterr().err


def test_serve_without_a_built_web_says_so(server, tmp_path: Path, capsys) -> None:
    cli.main(["serve", "--library", str(tmp_path), "--web", str(tmp_path / "none")])

    assert (
        f"stage serve: no built web in {tmp_path / 'none'} (npm run build in web/); "
        "serving the API only"
    ) in capsys.readouterr().err


def test_serve_with_a_built_web_serves_it(server, tmp_path: Path) -> None:
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<title>index</title>")
    server.use = lambda client: client.get("/karaoke").text

    cli.main(["serve", "--library", str(tmp_path), "--web", str(web)])

    assert server.result == "<title>index</title>"


def test_the_default_web_folder_is_web_dist_in_the_current_folder(
    server, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "web" / "dist").mkdir(parents=True)
    (tmp_path / "web" / "dist" / "index.html").write_text("<title>index</title>")
    server.use = lambda client: client.get("/karaoke/stage").text

    cli.main(["serve"])

    assert server.result == "<title>index</title>"
