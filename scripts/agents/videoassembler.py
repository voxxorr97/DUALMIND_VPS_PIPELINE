"""VideoAssembler agent for the DualMind v2.2 pipeline.

This fifth pipeline agent reads illustrated scripts from SQLite, combines their
four generated frames with the narration audio through ffmpeg, and marks the
script as assembled.

Usage examples::

    python scripts/agents/videoassembler.py
    python scripts/agents/videoassembler.py --limit 1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
DEFAULT_VIDEO_OUTPUT_DIR = REPO_ROOT / "output" / "videos"
DEFAULT_SUBTITLE_OUTPUT_DIR = REPO_ROOT / "output" / "subtitles"
LOG_PATH = REPO_ROOT / "logs" / "videoassembler.log"
DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_LIMIT = 3
MAX_LIMIT = 3
FRAME_COUNT = 4
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_CRF = 23
AUDIO_BITRATE = "192k"
FFMPEG_PRESET = "fast"
WHISPER_MODEL = "base"
SUBTITLE_STYLE = (
    "FontName=Arial Bold,FontSize=18,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=80"
)


class IllustratedScript(TypedDict):
    """Illustrated script selected from ``scripts_generated``."""

    id: int
    title: str
    audio_path: str | None
    images_path: str | None
    video_path: str | None


class RunSummary(TypedDict):
    """Structured summary returned by the agent run."""

    ok: bool
    illustrated_found: int
    videos_created: int
    scripts_assembled: int
    skipped: int
    errors: int


def configure_logging() -> None:
    """Configure file logging for VideoAssembler runs."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_environment() -> None:
    """Load local ``.env`` values without overriding exported variables."""
    load_dotenv(DOTENV_PATH)


def env_flag(name: str, default: bool = False) -> bool:
    """Return a boolean value from an environment variable."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_db_path() -> Path:
    """Return the SQLite path, honoring ``SQLITE_DB_PATH`` from the environment."""
    configured = os.environ.get("SQLITE_DB_PATH")
    if not configured:
        return DEFAULT_DB_PATH
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def resolve_video_output_dir() -> Path:
    """Return the MP4 output directory, honoring ``VIDEOASSEMBLER_OUTPUT_DIR``."""
    configured = os.environ.get("VIDEOASSEMBLER_OUTPUT_DIR")
    if not configured:
        return DEFAULT_VIDEO_OUTPUT_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def resolve_subtitle_output_dir() -> Path:
    """Return the subtitle output directory, honoring ``VIDEOASSEMBLER_SUBTITLE_DIR``."""
    configured = os.environ.get("VIDEOASSEMBLER_SUBTITLE_DIR")
    if not configured:
        return DEFAULT_SUBTITLE_OUTPUT_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def get_connection() -> sqlite3.Connection:
    """Open the SQLite database and ensure VideoAssembler schema requirements exist."""
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create required table and add media path columns to older databases."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scripts_generated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER,
            title TEXT NOT NULL,
            script_text TEXT NOT NULL,
            platform TEXT NOT NULL,
            duration_seconds INTEGER,
            status TEXT NOT NULL DEFAULT 'draft',
            audio_path TEXT,
            images_path TEXT,
            video_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(scripts_generated)").fetchall()
    }
    changed = False
    for column_name in ("audio_path", "images_path", "video_path"):
        if column_name not in columns:
            connection.execute(f"ALTER TABLE scripts_generated ADD COLUMN {column_name} TEXT")
            changed = True
    if changed:
        connection.commit()


def verify_binary(binary_name: str) -> str:
    """Verify that a system binary is available through ``which``."""
    result = subprocess.run(
        ["which", binary_name],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or binary_name


def verify_media_binaries() -> None:
    """Ensure ffmpeg and ffprobe are available before doing any work."""
    ffmpeg_path = verify_binary("ffmpeg")
    ffprobe_path = verify_binary("ffprobe")
    logging.info("Using ffmpeg=%s ffprobe=%s", ffmpeg_path, ffprobe_path)


def get_illustrated_scripts(connection: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> list[IllustratedScript]:
    """Fetch up to ``limit`` scripts ready for video assembly."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    rows = connection.execute(
        """
        SELECT id, title, audio_path, images_path, video_path
        FROM scripts_generated
        WHERE status = 'illustrated'
        ORDER BY id ASC
        LIMIT ?
        """,
        (bounded_limit,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "audio_path": row["audio_path"],
            "images_path": row["images_path"],
            "video_path": row["video_path"],
        }
        for row in rows
    ]


def resolve_existing_path(raw_path: str | None) -> Path | None:
    """Resolve a DB path that may be absolute, repo-relative, or cwd-relative."""
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, Path.cwd() / path, path]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def parse_image_paths(raw_images_path: str | None) -> list[Path | None]:
    """Parse the JSON image path array and return exactly four optional paths."""
    images: list[Path | None] = []
    if raw_images_path:
        parsed = json.loads(raw_images_path)
        if not isinstance(parsed, list):
            raise ValueError("images_path must be a JSON array")
        for value in parsed[:FRAME_COUNT]:
            images.append(resolve_existing_path(str(value)) if value else None)
    while len(images) < FRAME_COUNT:
        images.append(None)
    return images[:FRAME_COUNT]


def to_repo_relative(path: Path) -> str:
    """Return a repository-relative path when possible."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def probe_audio_duration(audio_path: Path) -> float:
    """Return the real audio duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError(f"Invalid audio duration for {audio_path}: {duration}")
    return duration


def run_subprocess(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    """Run an ffmpeg/ffprobe command and log full stderr on failure."""
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logging.error("%s failed with exit code %s", label, exc.returncode)
        logging.error("%s stderr:\n%s", label, exc.stderr)
        raise


def create_segment_clip(image_path: Path | None, duration: float, output_path: Path) -> None:
    """Create one silent vertical MP4 segment from an image or black background."""
    video_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p"
    )
    if image_path is not None and image_path.exists():
        command = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image_path),
            "-vf",
            video_filter,
            "-r",
            "30",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            FFMPEG_PRESET,
            "-crf",
            str(VIDEO_CRF),
            str(output_path),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={duration:.3f}",
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            FFMPEG_PRESET,
            "-crf",
            str(VIDEO_CRF),
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    run_subprocess(command, f"ffmpeg segment {output_path.name}")


def write_concat_file(segment_paths: list[Path], concat_path: Path) -> None:
    """Write an ffmpeg concat demuxer file for generated segment clips."""
    lines = []
    for segment_path in segment_paths:
        escaped = str(segment_path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mux_video_audio(concat_path: Path, audio_path: Path, output_path: Path) -> None:
    """Concatenate video segments and mux the narration audio."""
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        FFMPEG_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run_subprocess(command, f"ffmpeg mux {output_path.name}")


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp."""
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt_from_segments(segments: list[dict[str, Any]], srt_path: Path) -> None:
    """Write Whisper transcription segments to an SRT file."""
    entries = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = float(segment.get("start", 0.0))
        end = max(float(segment.get("end", start + 1.0)), start + 0.1)
        entries.append(
            f"{len(entries) + 1}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}\n"
        )
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(entries), encoding="utf-8")


def transcribe_audio_to_srt(audio_path: Path, srt_path: Path) -> None:
    """Transcribe audio with openai-whisper and store an SRT file."""
    import whisper

    model = whisper.load_model(os.environ.get("WHISPER_MODEL", WHISPER_MODEL))
    result = model.transcribe(str(audio_path), language="fr")
    segments = result.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("Whisper result did not include a valid segments list")
    write_srt_from_segments(segments, srt_path)


def escape_subtitles_filter_path(path: Path) -> str:
    """Escape a path for ffmpeg's subtitles filter argument."""
    escaped = str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return escaped


def burn_subtitles(input_video_path: Path, srt_path: Path, output_video_path: Path) -> None:
    """Burn SRT subtitles into an MP4 with ffmpeg's subtitles filter."""
    filter_value = (
        f"subtitles='{escape_subtitles_filter_path(srt_path)}':"
        f"force_style='{SUBTITLE_STYLE}'"
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video_path),
        "-vf",
        filter_value,
        "-c:v",
        "libx264",
        "-preset",
        FFMPEG_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video_path),
    ]
    run_subprocess(command, f"ffmpeg subtitles {output_video_path.name}")


def assemble_video(script: IllustratedScript, burn_subtitle_flag: bool) -> Path:
    """Assemble one illustrated script into the final MP4 file."""
    audio_path = resolve_existing_path(script["audio_path"])
    if audio_path is None or not audio_path.exists():
        raise FileNotFoundError(f"Missing audio file for script {script['id']}: {script['audio_path']}")

    image_paths = parse_image_paths(script["images_path"])
    video_output_dir = resolve_video_output_dir()
    subtitle_output_dir = resolve_subtitle_output_dir()
    video_output_dir.mkdir(parents=True, exist_ok=True)
    subtitle_output_dir.mkdir(parents=True, exist_ok=True)

    final_path = video_output_dir / f"{script['id']}.mp4"
    duration = probe_audio_duration(audio_path)
    segment_duration = duration / FRAME_COUNT

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"videoassembler_{script['id']}_", dir=video_output_dir))
    success = False
    try:
        segment_paths = []
        for index, image_path in enumerate(image_paths, start=1):
            segment_path = tmp_dir / f"segment_{index}.mp4"
            create_segment_clip(image_path, segment_duration, segment_path)
            segment_paths.append(segment_path)

        concat_path = tmp_dir / "segments.txt"
        write_concat_file(segment_paths, concat_path)
        assembled_path = tmp_dir / "assembled.mp4" if burn_subtitle_flag else final_path
        mux_video_audio(concat_path, audio_path, assembled_path)

        if burn_subtitle_flag:
            srt_path = subtitle_output_dir / f"{script['id']}.srt"
            transcribe_audio_to_srt(audio_path, srt_path)
            subtitled_path = tmp_dir / "subtitled.mp4"
            burn_subtitles(assembled_path, srt_path, subtitled_path)
            shutil.move(str(subtitled_path), final_path)

        success = True
        logging.info(
            "Assembled script %s into %s (audio_duration=%.3fs, segment_duration=%.3fs)",
            script["id"],
            final_path,
            duration,
            segment_duration,
        )
        return final_path
    finally:
        if success:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def mark_script_assembled(connection: sqlite3.Connection, script_id: int, video_path: Path) -> None:
    """Persist the video path and transition an illustrated script to assembled."""
    with connection:
        connection.execute(
            """
            UPDATE scripts_generated
            SET status = 'assembled', video_path = ?
            WHERE id = ? AND status = 'illustrated'
            """,
            (to_repo_relative(video_path), script_id),
        )


def run_agent(limit: int = DEFAULT_LIMIT) -> RunSummary:
    """Run VideoAssembler for up to three illustrated scripts."""
    load_environment()
    verify_media_binaries()
    burn_subtitle_flag = env_flag("BURN_SUBTITLES", default=False)
    resolve_video_output_dir().mkdir(parents=True, exist_ok=True)
    resolve_subtitle_output_dir().mkdir(parents=True, exist_ok=True)

    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    summary: RunSummary = {
        "ok": True,
        "illustrated_found": 0,
        "videos_created": 0,
        "scripts_assembled": 0,
        "skipped": 0,
        "errors": 0,
    }

    with get_connection() as connection:
        scripts = get_illustrated_scripts(connection, bounded_limit)
        summary["illustrated_found"] = len(scripts)
        logging.info(
            "VideoAssembler run started: %s illustrated script(s), limit=%s, burn_subtitles=%s",
            len(scripts),
            bounded_limit,
            burn_subtitle_flag,
        )

        for script in scripts:
            try:
                video_path = assemble_video(script, burn_subtitle_flag)
                mark_script_assembled(connection, script["id"], video_path)
                summary["videos_created"] += 1
                summary["scripts_assembled"] += 1
            except Exception as exc:  # noqa: BLE001 - keep agent resilient and continue next script.
                summary["errors"] += 1
                summary["skipped"] += 1
                logging.exception("Skipping script %s after video assembly error: %s", script["id"], exc)

    summary["ok"] = summary["errors"] == 0
    logging.info("VideoAssembler run finished: %s", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Assemble videos for illustrated DualMind scripts.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum videos to assemble this run, capped at {MAX_LIMIT}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    configure_logging()
    args = parse_args(argv)
    try:
        summary = run_agent(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - return JSON failure for schedulers.
        logging.exception("VideoAssembler fatal error: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
