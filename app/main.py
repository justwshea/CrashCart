"""FastAPI service for mock and live CrashCart incident runs."""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.events import SAMPLES_DIR, EventRecorder, read_events, replay
from app.ingest import load_bundle
from app.limits import limiter
from app.pipeline import run as run_pipeline


app = FastAPI(title="CrashCart")
_reports: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_TOKENS = 1_200_000
ALLOWED_EXTENSIONS = {".log", ".txt", ".yaml", ".yml", ".png"}


class RunRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=80)
    swarm_concurrency: int = Field(default=24, ge=1, le=48)
    use_eyes: Optional[bool] = None


def _incident_root(incident_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", incident_id):
        raise HTTPException(400, "invalid incident id")
    base = Path(settings.incidents_dir).resolve()
    target = (base / incident_id).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "incident path escapes sample directory")
    if not target.is_dir():
        raise HTTPException(404, "incident not found")
    return target


@app.get("/")
async def index():
    path = Path(__file__).parent / "static" / "index.html"
    if not path.is_file():
        return JSONResponse({"service": "CrashCart", "mode": settings.mode})
    return FileResponse(path)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "mode": settings.mode}


@app.get("/api/incidents")
async def incidents():
    root = Path(settings.incidents_dir)
    if not root.is_dir():
        return []
    result = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            bundle = load_bundle(folder)
        except (OSError, ValueError):
            continue
        result.append({"id": folder.name, "title": folder.name.replace("_", " "),
                       "file_count": len(bundle.files), "est_tokens": bundle.est_tokens,
                       "has_dashboard": any(item.kind == "dashboard" for item in bundle.files),
                       "has_truth": (folder / "truth.json").is_file()})
    return result


async def _run_background(run_id: str, incident_id: str, root: Path, options: dict) -> None:
    recorder = EventRecorder(run_id, settings.runs_dir)
    try:
        bundle = load_bundle(root)
        _reports[run_id] = await run_pipeline(bundle, {**options, "run_id": run_id,
            "incident_id": incident_id, "run_dir": recorder.directory}, recorder)
    except Exception as exc:
        _reports[run_id] = {"run_id": run_id, "incident_id": incident_id, "error": str(exc)}
        await recorder("error", {"message": str(exc)})


@app.post("/api/run")
async def start_run(body: RunRequest, request: Request):
    root = _incident_root(body.incident_id)
    ip = request.client.host if request.client else "unknown"
    allowed, replay_id = limiter.reserve(ip)
    if not allowed:
        return JSONResponse(status_code=429, content={"error": "limit", "replay_run_id": replay_id})
    run_id = uuid.uuid4().hex
    _reports.pop(run_id, None)
    options = {"swarm_concurrency": body.swarm_concurrency,
               "use_eyes": settings.use_eyes and body.use_eyes is not False}
    _tasks[run_id] = asyncio.create_task(_run_background(run_id, body.incident_id, root, options))
    return {"run_id": run_id}


async def _sse_file(run_id: str):
    path = Path(settings.runs_dir) / run_id / "events.jsonl"
    offset = 0
    ping_at = asyncio.get_event_loop().time() + 15
    while True:
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                lines = handle.readlines()
                offset = handle.tell()
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        task = _tasks.get(run_id)
        if task is None or task.done():
            break
        now = asyncio.get_event_loop().time()
        if now >= ping_at:
            yield ": ping\n\n"
            ping_at = now + 15
        await asyncio.sleep(.25)


@app.get("/api/stream/{run_id}")
async def stream(run_id: str):
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(400, "invalid run id")
    if run_id not in _tasks and not read_events(run_id):
        raise HTTPException(404, "run not found")
    return StreamingResponse(_sse_file(run_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/replay/{run_id}")
async def replay_route(run_id: str, speed: float = Query(default=1.0, gt=0)):
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(400, "invalid run id")
    events = read_events(run_id)
    if not events:
        raise HTTPException(404, "replay not found")

    async def generate():
        async for event in replay(run_id, speed):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/replays")
async def available_replays():
    return [{"id": path.stem, "title": path.stem.replace("_", " ")}
            for path in sorted(SAMPLES_DIR.glob("*.jsonl"))]


@app.get("/api/report/{run_id}")
async def report(run_id: str):
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(400, "invalid run id")
    result = _reports.get(run_id)
    if result is None:
        raise HTTPException(404, "report not ready")
    return result


@app.get("/api/report/{run_id}/postmortem.md")
async def postmortem(run_id: str):
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(400, "invalid run id")
    path = Path(settings.runs_dir) / run_id / "postmortem.md"
    if not path.is_file():
        raise HTTPException(404, "postmortem not ready")
    return FileResponse(path, media_type="text/markdown", filename="postmortem.md")


@app.get("/api/source")
async def source(incident: str, file: str, line: int, ctx: int = 15):
    root = _incident_root(incident)
    candidate = (root / file).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(400, "source path escapes incident directory")
    if not candidate.is_file():
        raise HTTPException(404, "source file not found")
    if candidate.suffix.lower() == ".png":
        raise HTTPException(400, "dashboard has no numbered text lines")
    lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    if line < 1 or line > len(lines):
        raise HTTPException(404, "line out of range")
    context = max(0, min(ctx, 100))
    start, end = max(1, line - context), min(len(lines), line + context)
    return {"file": candidate.relative_to(root).as_posix(), "line": line,
            "line_start": start, "line_end": end,
            "lines": [{"number": number, "text": lines[number - 1]}
                      for number in range(start, end + 1)]}


@app.get("/api/dashboard/{incident_id}")
async def dashboard(incident_id: str):
    root = _incident_root(incident_id)
    path = (root / "dashboard.png").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "dashboard not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/truth/{incident_id}")
async def truth(incident_id: str):
    root = _incident_root(incident_id)
    path = root / "truth.json"
    if not path.is_file():
        raise HTTPException(404, "truth badge unavailable")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "archive exceeds 30 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError):
        raise HTTPException(400, "invalid zip archive")
    entries = [item for item in archive.infolist() if not item.is_dir()]
    total_uncompressed = sum(item.file_size for item in entries)
    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise HTTPException(413, "archive uncompressed size exceeds limit")
    token_estimate = 0
    png_count = 0
    for item in entries:
        normalized = item.filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", normalized):
            raise HTTPException(400, "archive contains an unsafe path")
        if item.filename.lower().endswith("truth.json"):
            raise HTTPException(400, "truth.json is not accepted in uploads")
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"unsupported upload file: {path.name}")
        mode = item.external_attr >> 16
        if mode and (mode & 0o170000) == 0o120000:
            raise HTTPException(400, "archive symlinks are not accepted")
        if path.suffix.lower() == ".png":
            png_count += 1
        else:
            token_estimate += item.file_size // 4
    if png_count > 1:
        raise HTTPException(400, "only one dashboard PNG is accepted")
    if token_estimate > MAX_UPLOAD_TOKENS:
        raise HTTPException(413, "estimated upload tokens exceed 1.2M")

    identifier = "upload-" + uuid.uuid4().hex[:12]
    incidents_root = Path(settings.incidents_dir).resolve()
    target = incidents_root / identifier
    target.mkdir(parents=True, exist_ok=False)
    try:
        for item in entries:
            relative = PurePosixPath(item.filename.replace("\\", "/"))
            destination = (target / Path(*relative.parts)).resolve()
            if target not in destination.parents:
                raise HTTPException(400, "archive path escapes upload directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source_handle, destination.open("wb") as out_handle:
                shutil.copyfileobj(source_handle, out_handle)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"incident_id": identifier, "file_count": len(entries), "est_tokens": token_estimate}
