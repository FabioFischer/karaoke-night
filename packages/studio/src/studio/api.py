"""The Studio API of docs/PLAN.md section 5.3: jobs, library, files and deletion."""

import secrets
import threading
from pathlib import Path
from typing import Annotated

from core.studio_api import DeleteResult, Job, JobCreated, JobRequest, SongSummary
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse

from studio import library as lib
from studio.jobs import JobQueue
from studio.package import song_id


def create_app(library: Path, token: str, queue: JobQueue) -> FastAPI:
    expected = token.encode("utf-8")

    def check_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            given.encode("utf-8"), expected
        ):
            raise HTTPException(
                401, "missing or wrong token", headers={"WWW-Authenticate": "Bearer"}
            )

    # The generated docs routes would be the only ones without the token.
    app = FastAPI(
        title="karaoke-night Studio",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(check_token)],
    )
    # The active job check and the enqueue happen together, so two requests
    # for the same video cannot both start a job.
    submitting = threading.Lock()

    def package_of(song: str) -> lib.Package:
        package = lib.scan(library).get(song)
        if package is None:
            raise HTTPException(404, "unknown song")
        return package

    @app.post("/jobs")
    def create_job(request: JobRequest) -> JobCreated:
        video_id = request.source.video_id
        with submitting:
            job = queue.active(video_id)
            if job is None:
                existing = lib.find_video(library, video_id)
                if existing is not None:
                    job = queue.add_done(existing)
                else:
                    song = song_id(request.title, request.artist, video_id)
                    job = queue.submit(request, song)
        return JobCreated(job_id=job.job_id, song_id=job.song_id)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> Job:
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    @app.get("/songs")
    def list_songs() -> list[SongSummary]:
        return [
            SongSummary(
                id=package_id,
                title=package.manifest.title,
                artist=package.manifest.artist,
                duration_s=package.manifest.duration_s,
                pipeline_version=package.manifest.pipeline_version,
                created_at=package.manifest.created_at,
            )
            for package_id, package in lib.scan(library).items()
        ]

    @app.get("/songs/{song}/manifest")
    def get_manifest(song: str) -> Response:
        # As stored: writing the model back would drop fields it does not know.
        return Response(package_of(song).manifest_bytes, media_type="application/json")

    @app.get("/songs/{song}/files/{path:path}")
    def get_file(song: str, path: str) -> FileResponse:
        package = package_of(song)
        if path not in package.files() or not (package.folder / path).is_file():
            raise HTTPException(404, "unknown file")
        return FileResponse(package.folder / path)

    @app.delete("/songs/{song}", status_code=204)
    def delete_song(song: str) -> None:
        try:
            lib.delete(package_of(song))
        except lib.Busy:
            raise HTTPException(
                409, "a file of the song is open; nothing deleted"
            ) from None

    @app.delete("/songs")
    def delete_all() -> DeleteResult:
        result = DeleteResult(deleted=[], busy=[])
        for package_id, package in lib.scan(library).items():
            try:
                lib.delete(package)
            except lib.Busy:
                result.busy.append(package_id)
            else:
                result.deleted.append(package_id)
        return result

    return app
