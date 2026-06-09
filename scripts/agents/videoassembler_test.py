"""Standalone offline smoke test for the VideoAssembler agent.

The test creates a temporary SQLite database, deterministic local media fixtures,
inserts one illustrated script, runs the real ffmpeg assembly path, and validates
that the MP4 path is persisted.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import sys
import tempfile
import wave
import zlib
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

TEST_ROOT = Path(tempfile.gettempdir()) / "dualmind_videoassembler_test"
TEST_DB_PATH = TEST_ROOT / "dualmind.db"
TEST_AUDIO_PATH = TEST_ROOT / "audio" / "test_audio.wav"
TEST_IMAGE_DIR = TEST_ROOT / "images" / "script_1"
TEST_VIDEO_OUTPUT_DIR = TEST_ROOT / "videos"
TEST_SUBTITLE_OUTPUT_DIR = TEST_ROOT / "subtitles"
os.environ["SQLITE_DB_PATH"] = str(TEST_DB_PATH)
os.environ["VIDEOASSEMBLER_OUTPUT_DIR"] = str(TEST_VIDEO_OUTPUT_DIR)
os.environ["VIDEOASSEMBLER_SUBTITLE_DIR"] = str(TEST_SUBTITLE_OUTPUT_DIR)
os.environ["BURN_SUBTITLES"] = "false"

import videoassembler


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Return one PNG chunk."""
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack("!I", len(data)) + chunk_type + data + struct.pack("!I", checksum)


def write_solid_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Write a solid RGB PNG using only the Python standard library."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scanline = b"\x00" + bytes(rgb) * width
    raw_pixels = scanline * height
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"".join(
        [
            png_chunk("IHDR".encode(), struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            png_chunk("IDAT".encode(), zlib.compress(raw_pixels, level=9)),
            png_chunk("IEND".encode(), b""),
        ]
    )
    path.write_bytes(png_bytes)


def write_sine_wave(path: Path, duration_seconds: float = 10.0, frequency: float = 440.0) -> None:
    """Write a mono 16-bit PCM WAV sine wave using stdlib wave+math."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44_100
    amplitude = 16_000
    total_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for index in range(total_frames):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            wav_file.writeframes(struct.pack("<h", sample))


def reset_test_environment() -> tuple[int, list[str]]:
    """Create a fresh temporary DB and local media fixtures."""
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)

    write_sine_wave(TEST_AUDIO_PATH, duration_seconds=10.0)
    colors = [(180, 20, 20), (20, 150, 60), (20, 80, 190), (190, 160, 20)]
    image_paths = []
    for index, color in enumerate(colors, start=1):
        image_path = TEST_IMAGE_DIR / f"frame_{index}.png"
        write_solid_png(image_path, 360, 640, color)
        image_paths.append(str(image_path))

    with videoassembler.get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scripts_generated (
                topic_id, title, script_text, platform, duration_seconds, status, audio_path, images_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                "Le signal impossible du lac noir",
                "Un dossier non classé raconte un signal reçu depuis un lac désert.",
                "youtube_shorts_tiktok",
                10,
                "illustrated",
                str(TEST_AUDIO_PATH),
                json.dumps(image_paths, ensure_ascii=False),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid), image_paths


def fetch_result(script_id: int) -> dict[str, Any]:
    """Return the updated script row after VideoAssembler execution."""
    with videoassembler.get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, title, status, audio_path, images_path, video_path
            FROM scripts_generated
            WHERE id = ?
            """,
            (script_id,),
        ).fetchone()
    return dict(row) if row is not None else {}


def assert_expected_results(summary: videoassembler.RunSummary, result: dict[str, Any]) -> Path:
    """Validate that the MP4 exists and the SQLite row was assembled."""
    if not summary["ok"]:
        raise AssertionError(f"Agent summary is not ok: {summary}")
    if summary["illustrated_found"] != 1 or summary["videos_created"] != 1:
        raise AssertionError(f"Unexpected summary counts: {summary}")
    if result.get("status") != "assembled":
        raise AssertionError(f"Expected status assembled, got: {result}")
    raw_video_path = result.get("video_path")
    if not raw_video_path:
        raise AssertionError(f"video_path was not updated: {result}")
    video_path = Path(raw_video_path)
    if not video_path.is_absolute():
        video_path = videoassembler.REPO_ROOT / video_path
    if not video_path.exists():
        video_path = TEST_VIDEO_OUTPUT_DIR / f"{result['id']}.mp4"
    if not video_path.exists():
        raise AssertionError(f"Expected MP4 file to exist: {raw_video_path}")
    return video_path


def main() -> int:
    """Run VideoAssembler with deterministic local fixtures."""
    videoassembler.configure_logging()
    script_id, image_paths = reset_test_environment()

    summary = videoassembler.run_agent(limit=1)
    result = fetch_result(script_id)
    video_path = assert_expected_results(summary, result)
    duration = videoassembler.probe_audio_duration(TEST_AUDIO_PATH)
    size_bytes = video_path.stat().st_size

    print(f"Base SQLite temporaire: {TEST_DB_PATH}")
    print(f"Audio WAV temporaire: {TEST_AUDIO_PATH}")
    print(f"Dossier vidéos temporaire: {TEST_VIDEO_OUTPUT_DIR}")
    print("\nImages PNG générées:")
    print(json.dumps(image_paths, ensure_ascii=False, indent=2))
    print("\nRésumé agent:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nScript mis à jour:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nMP4 généré: {video_path}")
    print(f"Durée audio détectée: {duration:.3f}s")
    print(f"Taille MP4: {size_bytes} octets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
