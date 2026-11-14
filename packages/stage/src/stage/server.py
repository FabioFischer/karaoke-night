"""The Stage API behind the TV screens (docs/PLAN.md section 5.8).

Every route answers JSON under /api. A refusal carries a stable code for the
screen to put into words and an English detail for the logs:
{"error": code, "detail": text}. When a built web is given, a GET of any
path outside /api that no route matches serves it, with index.html for the
client routes such as /karaoke/stage.
"""

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from stage import library as lib
from stage.lyrics import song_lyrics
from stage.playlist import Entry, NotAddable, Playlist
from stage.search import Found, Origin, Result, library_results
from stage.session import Session, SessionError

STATUS = {
    "unknown_entry": 404,
    "unknown_song": 404,
    "no_song": 409,
    "not_ready": 409,
    "wrong_state": 409,
    "entry_playing": 409,
    "not_addable": 422,
    "invalid_request": 422,
    "reaper_unreachable": 502,
    "listener_silent": 502,
    "reaper_failed": 502,
}
# The codes of the errors the routing itself answers.
HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class AddRequest(BaseModel):
    """A search or library result, as GET /api/search and /api/library give it."""

    title: str
    artist: str
    origin: Origin
    video_id: str | None = None
    album: str | None = None
    duration_s: float | None = None
    thumbnail_url: str | None = None
    song_id: str | None = None


class Settings(BaseModel):
    muted: list[str]
    key: int


def refusal(code: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=STATUS[code])


def entry_json(entry: Entry) -> dict:
    return {
        "id": entry.id,
        "state": entry.state,
        "step": entry.step,
        "progress": None if entry.progress is None else asdict(entry.progress),
        "error": entry.error,
        "detail": entry.detail,
        "song": asdict(entry.song),
    }


def create_app(
    library: Path,
    playlist: Playlist,
    session: Session,
    *,
    web: Path | None,
    find: Callable[[str], Found],
) -> FastAPI:
    """web is the built web folder (holding index.html), or None for the API only."""
    app = FastAPI(
        title="karaoke-night Stage",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, error: RequestValidationError) -> Response:
        return refusal("invalid_request", str(error.errors()))

    @app.exception_handler(HTTPException)
    def http_refused(request: Request, error: HTTPException) -> Response:
        if error.status_code == 404 and web is not None and _wants_web(request):
            return _web_file(web.resolve(), request.url.path.lstrip("/"))
        return JSONResponse(
            {
                "error": HTTP_CODES.get(error.status_code, "http_error"),
                "detail": f"{request.method} {request.url.path}: {error.detail}",
            },
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.exception_handler(SessionError)
    def session_refused(request: Request, error: SessionError) -> Response:
        return refusal(error.code, error.detail)

    def stems(entry_id: str | None) -> list[str]:
        """The stems of the entry's package, none while it is not in the library."""
        entry = None if entry_id is None else playlist.get(entry_id)
        if entry is None or entry.song.song_id is None:
            return []
        song = lib.scan(library).get(entry.song.song_id)
        return [] if song is None else list(song.manifest.stems)

    def state() -> dict:
        view = session.view()
        return {
            "session": {
                **asdict(view),
                "muted": list(view.muted),
                "stems": stems(view.entry_id),
            },
            "playlist": [entry_json(entry) for entry in playlist.entries()],
        }

    @app.get("/api/state")
    def get_state() -> dict:
        return state()

    @app.get("/api/search")
    def search(q: str) -> dict:
        found = find(q)
        return {
            "results": [asdict(result) for result in found.results],
            "notice": None if found.notice is None else "offline",
            "detail": found.notice,
        }

    @app.get("/api/library")
    def list_library() -> dict:
        return {"songs": [asdict(result) for result in library_results(library)]}

    @app.get("/api/library/{song_id}/lyrics")
    def get_lyrics(song_id: str) -> Response:
        song = lib.scan(library).get(song_id)
        if song is None:
            return refusal("unknown_song", f"no song {song_id} in the library")
        lyrics = song_lyrics(song)
        return JSONResponse(
            {"status": lyrics.status, "lines": [asdict(line) for line in lyrics.lines]}
        )

    @app.post("/api/playlist", status_code=201)
    def add_entry(request: AddRequest) -> Response:
        try:
            entry = playlist.add(Result(**request.model_dump()))
        except NotAddable as error:
            return refusal("not_addable", str(error))
        return JSONResponse(entry_json(entry), status_code=201)

    @app.delete("/api/playlist/{entry_id}", status_code=204)
    def remove_entry(entry_id: str) -> None:
        session.remove(entry_id)

    @app.put("/api/session/settings")
    def configure(settings: Settings) -> dict:
        session.configure(settings.muted, settings.key)
        return state()

    actions = {
        "start": session.start,
        "stop": session.stop,
        "play_next": session.play_next,
        "setup_next": session.setup_next,
        "skip": session.skip,
    }
    for name, action in actions.items():
        app.add_api_route(f"/api/session/{name}", _run(action, state), methods=["POST"])

    return app


def _run(action: Callable[[], None], state: Callable[[], dict]) -> Callable[[], dict]:
    def run() -> dict:
        action()
        return state()

    return run


def _wants_web(request: Request) -> bool:
    path = request.url.path
    api = path == "/api" or path.startswith("/api/")
    return request.method in ("GET", "HEAD") and not api


def _web_file(root: Path, path: str) -> Response:
    target = (root / path).resolve()
    if path and target.is_relative_to(root) and target.is_file():
        return FileResponse(target)
    # A client route: the web app reads the path itself.
    return FileResponse(root / "index.html")
