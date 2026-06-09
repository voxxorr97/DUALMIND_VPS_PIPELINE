"""Imagegen agent for the DualMind v2.2 pipeline.

This fourth pipeline agent reads voiced scripts from SQLite, extracts the four
Scriptwriter sections, builds English Flux.1 prompts, generates vertical PNG
frames with Replicate, stores them under ``output/images/{script_id}``, and
marks scripts as illustrated.

Usage examples::

    python scripts/agents/imagegen.py
    python scripts/agents/imagegen.py --limit 1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Protocol, TypedDict

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "images"
LOG_PATH = REPO_ROOT / "logs" / "imagegen.log"
DOTENV_PATH = REPO_ROOT / ".env"
MODEL_SLUG = "black-forest-labs/flux-schnell"
REPLICATE_API_BASE = "https://api.replicate.com/v1"
DEFAULT_LIMIT = 2
MAX_LIMIT = 2
POLL_INTERVAL_SECONDS = 2
PREDICTION_TIMEOUT_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 30
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 60
IMAGE_WIDTH = 768
IMAGE_HEIGHT = 1344
NUM_INFERENCE_STEPS = 4
OUTPUT_FORMAT = "png"
OUTPUT_QUALITY = 90
STYLE_SUFFIX = (
    "cinematic, dark atmosphere, mysterious, french countryside or urban noir, "
    "dramatic lighting, photorealistic, 9:16 vertical format, no text, no watermark"
)
SEGMENT_ORDER = ["HOOK", "DÉVELOPPEMENT", "RÉVÉLATION", "CTA"]
SEGMENT_ALIASES = {
    "HOOK": ("HOOK", "ACCROCHE"),
    "DÉVELOPPEMENT": ("DÉVELOPPEMENT", "DEVELOPPEMENT", "DEVELOPMENT"),
    "RÉVÉLATION": ("RÉVÉLATION", "REVELATION"),
    "CTA": ("CTA", "CALL TO ACTION"),
}
SECTION_RE = re.compile(
    r"^\s*\[(?P<label>HOOK|ACCROCHE|D[ÉE]VELOPPEMENT|DEVELOPMENT|R[ÉE]V[ÉE]LATION|CTA|CALL TO ACTION)\]"
    r"\s*(?:\([^)]*\))?\s*:?(?P<text>.*)$",
    flags=re.IGNORECASE | re.MULTILINE,
)
WHITESPACE_RE = re.compile(r"\s+")


class VoicedScript(TypedDict):
    """Voiced script selected from ``scripts_generated``."""

    id: int
    title: str
    script_text: str
    images_path: str | None


class SegmentPrompt(TypedDict):
    """Visual prompt data for one video section."""

    segment: str
    text: str
    prompt: str


class RunSummary(TypedDict):
    """Structured summary returned by the agent run."""

    ok: bool
    voiced_found: int
    images_created: int
    scripts_illustrated: int
    skipped: int
    errors: int


class ImageClient(Protocol):
    """Minimal image generation client used by the agent and standalone test."""

    def generate_to_file(self, prompt: str, output_path: Path) -> None:
        """Generate one PNG image for ``prompt`` into ``output_path``."""


def configure_logging() -> None:
    """Configure file logging for Imagegen runs."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_environment() -> None:
    """Load local ``.env`` values without overriding exported variables."""
    load_dotenv(DOTENV_PATH)


def resolve_db_path() -> Path:
    """Return the SQLite path, honoring ``SQLITE_DB_PATH`` from the environment."""
    configured = os.environ.get("SQLITE_DB_PATH")
    if not configured:
        return DEFAULT_DB_PATH
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def resolve_output_dir() -> Path:
    """Return the image output directory, honoring ``IMAGEGEN_OUTPUT_DIR`` if set."""
    configured = os.environ.get("IMAGEGEN_OUTPUT_DIR")
    if not configured:
        return DEFAULT_OUTPUT_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def get_connection() -> sqlite3.Connection:
    """Open the SQLite database and ensure Imagegen schema requirements exist."""
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create required tables and add ``images_path`` to older databases."""
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(scripts_generated)").fetchall()
    }
    changed = False
    if "audio_path" not in columns:
        connection.execute("ALTER TABLE scripts_generated ADD COLUMN audio_path TEXT")
        changed = True
    if "images_path" not in columns:
        connection.execute("ALTER TABLE scripts_generated ADD COLUMN images_path TEXT")
        changed = True
    if changed:
        connection.commit()


def get_voiced_scripts(connection: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> list[VoicedScript]:
    """Fetch up to ``limit`` scripts that are ready for image generation."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    rows = connection.execute(
        """
        SELECT id, title, script_text, images_path
        FROM scripts_generated
        WHERE status = 'voiced'
        ORDER BY id ASC
        LIMIT ?
        """,
        (bounded_limit,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "script_text": str(row["script_text"]),
            "images_path": row["images_path"],
        }
        for row in rows
    ]


def normalize_label(label: str) -> str | None:
    """Map a raw section marker label to the canonical segment name."""
    upper_label = label.upper().replace("É", "E").strip()
    for canonical, aliases in SEGMENT_ALIASES.items():
        normalized_aliases = {alias.upper().replace("É", "E") for alias in aliases}
        if upper_label in normalized_aliases:
            return canonical
    return None


def clean_segment_text(text: str) -> str:
    """Normalize whitespace in extracted script section text."""
    return WHITESPACE_RE.sub(" ", text).strip(" :-\t\n\r")


def extract_segments(script_text: str, title: str) -> dict[str, str]:
    """Extract HOOK, DÉVELOPPEMENT, RÉVÉLATION, and CTA sections with title fallback."""
    matches = list(SECTION_RE.finditer(script_text))
    segments = {segment: "" for segment in SEGMENT_ORDER}
    if not matches:
        return {segment: title for segment in SEGMENT_ORDER}

    for index, match in enumerate(matches):
        canonical = normalize_label(match.group("label"))
        if canonical is None or canonical not in segments:
            continue
        start = match.start("text")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script_text)
        section_text = clean_segment_text(script_text[start:end])
        if section_text:
            segments[canonical] = section_text

    return {segment: text or title for segment, text in segments.items()}


def choose_visual_anchor(text: str, segment: str) -> str:
    """Choose an English visual concept from French mystery script keywords."""
    lowered = text.lower()
    keyword_anchors = [
        (("ferme", "grange", "champ", "village"), "abandoned farmhouse at night, mist over empty fields"),
        (("phare", "côte", "mer", "océan", "bateau"), "lonely lighthouse on a stormy coast, cold sea spray and fog"),
        (("forêt", "bois", "sentier"), "dark forest path at dusk, wet leaves, a distant silhouette"),
        (("train", "gare", "rail"), "empty rural train station at midnight, one platform light glowing"),
        (("appartement", "immeuble", "paris", "rue", "ville"), "rainy urban noir street in France, old apartment windows, neon reflections"),
        (("archives", "dossier", "registre", "photo"), "dusty police archive room, open case files and old photographs"),
        (("disparu", "disparition", "introuvable", "trace"), "empty road with an abandoned car, fog swallowing the headlights"),
        (("appel", "radio", "message", "téléphone", "telephone"), "old radio receiver in a dark room, signal light glowing"),
        (("lumière", "lampe", "fenêtre", "fenetre"), "single warm light in a dark window, thick night fog outside"),
        (("cimetière", "tombe", "chapelle"), "old cemetery chapel under moonlight, low fog between stones"),
    ]
    for keywords, anchor in keyword_anchors:
        if any(keyword in lowered for keyword in keywords):
            return anchor

    fallback_by_segment = {
        "HOOK": "unsettling opening scene, isolated French road at night, a mystery about to begin",
        "DÉVELOPPEMENT": "investigation montage, shadowed clues in a French village, tense atmosphere",
        "RÉVÉLATION": "dramatic reveal of hidden evidence, cold light on an unsolved case file",
        "CTA": "final haunting shot, empty place after midnight, unresolved mystery lingering",
    }
    return fallback_by_segment[segment]


def build_visual_prompt(segment: str, text: str, title: str) -> str:
    """Build one English Flux prompt for a script segment."""
    source_text = text or title
    anchor = choose_visual_anchor(source_text, segment)
    segment_context = {
        "HOOK": "compelling mystery hook",
        "DÉVELOPPEMENT": "investigative development scene",
        "RÉVÉLATION": "shocking revelation scene",
        "CTA": "final question scene",
    }[segment]
    return f"{anchor}, {segment_context}, {STYLE_SUFFIX}"


def build_segment_prompts(script: VoicedScript) -> list[SegmentPrompt]:
    """Create four ordered visual prompts for a voiced script."""
    segments = extract_segments(script["script_text"], script["title"])
    return [
        {
            "segment": segment,
            "text": segments[segment],
            "prompt": build_visual_prompt(segment, segments[segment], script["title"]),
        }
        for segment in SEGMENT_ORDER
    ]


def relative_to_repo(path: Path) -> str:
    """Return a stable repository-relative path when possible."""
    return str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)


class ReplicateFluxClient:
    """Small requests-based Replicate Flux.1 client."""

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token

    def generate_to_file(self, prompt: str, output_path: Path) -> None:
        """Create a Replicate prediction, poll it, download the PNG output."""
        prediction = self._create_prediction(prompt)
        completed = self._poll_prediction(prediction)
        image_url = self._extract_image_url(completed)
        self._download_image(image_url, output_path)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Prefer": "wait=0",
        }

    def _create_prediction(self, prompt: str) -> dict[str, Any]:
        response = requests.post(
            f"{REPLICATE_API_BASE}/models/{MODEL_SLUG}/predictions",
            headers=self._headers(),
            json={
                "input": {
                    "prompt": prompt,
                    "width": IMAGE_WIDTH,
                    "height": IMAGE_HEIGHT,
                    "num_inference_steps": NUM_INFERENCE_STEPS,
                    "output_format": OUTPUT_FORMAT,
                    "output_quality": OUTPUT_QUALITY,
                }
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Replicate create HTTP {response.status_code}: {response.text[:500]}")
        return dict(response.json())

    def _poll_prediction(self, prediction: dict[str, Any]) -> dict[str, Any]:
        prediction_url = prediction.get("urls", {}).get("get")
        if not prediction_url:
            prediction_id = prediction.get("id")
            if not prediction_id:
                raise RuntimeError(f"Replicate prediction response missing polling URL/id: {prediction}")
            prediction_url = f"{REPLICATE_API_BASE}/predictions/{prediction_id}"

        deadline = time.monotonic() + PREDICTION_TIMEOUT_SECONDS
        current = prediction
        while time.monotonic() < deadline:
            status = str(current.get("status", "")).lower()
            if status == "succeeded":
                return current
            if status in {"failed", "canceled"}:
                raise RuntimeError(f"Replicate prediction {status}: {current.get('error')}")
            time.sleep(POLL_INTERVAL_SECONDS)
            response = requests.get(
                prediction_url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Replicate poll HTTP {response.status_code}: {response.text[:500]}")
            current = dict(response.json())
        raise TimeoutError(f"Replicate prediction timed out after {PREDICTION_TIMEOUT_SECONDS}s")

    def _extract_image_url(self, prediction: dict[str, Any]) -> str:
        output = prediction.get("output")
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str):
                return first
        raise RuntimeError(f"Replicate prediction output does not contain an image URL: {output}")

    def _download_image(self, image_url: str, output_path: Path) -> None:
        response = requests.get(image_url, timeout=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            raise RuntimeError(f"Image download HTTP {response.status_code}: {response.text[:500]}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)


def get_replicate_client() -> ImageClient:
    """Create a real Replicate client from environment variables."""
    api_token = os.environ.get("REPLICATE_API_TOKEN")
    if not api_token:
        raise RuntimeError("REPLICATE_API_TOKEN is required in .env or the process environment.")
    return ReplicateFluxClient(api_token=api_token)


def generate_with_retry(client: ImageClient, prompt: str, output_path: Path) -> None:
    """Generate one image with one retry after an API failure."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            client.generate_to_file(prompt, output_path)
            return
        except Exception as exc:  # noqa: BLE001 - API clients raise heterogeneous exceptions.
            last_error = exc
            logging.warning("Replicate image generation failed on attempt %s/2: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f"Replicate image generation failed after retry: {last_error}")


def mark_script_illustrated(connection: sqlite3.Connection, script_id: int, image_paths: list[Path]) -> None:
    """Persist image paths and transition a voiced script to illustrated."""
    relative_paths = [relative_to_repo(path) for path in image_paths]
    with connection:
        connection.execute(
            """
            UPDATE scripts_generated
            SET status = 'illustrated', images_path = ?
            WHERE id = ? AND status = 'voiced'
            """,
            (json.dumps(relative_paths, ensure_ascii=False), script_id),
        )


def generate_script_images(client: ImageClient, script: VoicedScript, output_dir: Path) -> list[Path]:
    """Generate four ordered PNG images for one voiced script."""
    script_dir = output_dir / str(script["id"])
    script_dir.mkdir(parents=True, exist_ok=True)
    prompts = build_segment_prompts(script)
    image_paths: list[Path] = []
    for index, prompt_data in enumerate(prompts, start=1):
        image_path = script_dir / f"frame_{index}.png"
        logging.info(
            "Generating image for script %s segment %s: %s",
            script["id"],
            prompt_data["segment"],
            prompt_data["prompt"],
        )
        generate_with_retry(client, prompt_data["prompt"], image_path)
        image_paths.append(image_path)
    return image_paths


def run_agent(client: ImageClient | None = None, limit: int = DEFAULT_LIMIT) -> RunSummary:
    """Run Imagegen for up to two voiced scripts."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    output_dir = resolve_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: RunSummary = {
        "ok": True,
        "voiced_found": 0,
        "images_created": 0,
        "scripts_illustrated": 0,
        "skipped": 0,
        "errors": 0,
    }

    with get_connection() as connection:
        scripts = get_voiced_scripts(connection, bounded_limit)
        summary["voiced_found"] = len(scripts)
        logging.info("Imagegen run started: %s voiced script(s), limit=%s", len(scripts), bounded_limit)

        for script in scripts:
            try:
                if client is None:
                    load_environment()
                    client = get_replicate_client()
                image_paths = generate_script_images(client, script, output_dir)
                if len(image_paths) != len(SEGMENT_ORDER):
                    raise RuntimeError(f"Expected 4 generated images, got {len(image_paths)}")
                mark_script_illustrated(connection, script["id"], image_paths)
                summary["images_created"] += len(image_paths)
                summary["scripts_illustrated"] += 1
                logging.info("Generated illustrations for script %s: %s", script["id"], image_paths)
            except Exception as exc:  # noqa: BLE001 - keep agent resilient and continue next script.
                summary["errors"] += 1
                summary["skipped"] += 1
                logging.exception("Skipping script %s after image generation error: %s", script["id"], exc)
                script_dir = output_dir / str(script["id"])
                if script_dir.exists() and not any(script_dir.iterdir()):
                    shutil.rmtree(script_dir)

    summary["ok"] = summary["errors"] == 0
    logging.info("Imagegen run finished: %s", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Generate Flux.1 images for voiced DualMind scripts.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum scripts to illustrate this run, capped at {MAX_LIMIT}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    configure_logging()
    args = parse_args(argv)
    try:
        summary = run_agent(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - return JSON failure for schedulers.
        logging.exception("Imagegen fatal error: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
