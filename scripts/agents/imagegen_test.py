"""Standalone offline test for the DualMind Imagegen agent.

The test uses a temporary SQLite database and a mock Replicate client, so it
never consumes Replicate credits and does not require API keys.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

TEST_DB_PATH = Path(tempfile.gettempdir()) / "dualmind_imagegen_test.db"
TEST_OUTPUT_DIR = Path(tempfile.gettempdir()) / "dualmind_imagegen_images"
TEST_PLACEHOLDER_PATH = Path(tempfile.gettempdir()) / "dualmind_imagegen_placeholder.png"
os.environ["SQLITE_DB_PATH"] = str(TEST_DB_PATH)
os.environ["IMAGEGEN_OUTPUT_DIR"] = str(TEST_OUTPUT_DIR)

import imagegen

# 1x1 transparent PNG, used only as a local offline placeholder copied by the mock.
PLACEHOLDER_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff030000060005"
    "57bfab0000000049454e44ae426082"
)


class MockReplicateFluxClient:
    """Mock Flux client that copies a local PNG placeholder for each frame."""

    def __init__(self, placeholder_path: Path) -> None:
        self.placeholder_path = placeholder_path
        self.calls: list[dict[str, Any]] = []

    def generate_to_file(self, prompt: str, output_path: Path) -> None:
        """Record the call and copy the deterministic placeholder PNG."""
        self.calls.append({"prompt": prompt, "output_path": str(output_path)})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.placeholder_path, output_path)


def reset_test_environment() -> int:
    """Create a fresh temporary database with one voiced fake script."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    if TEST_OUTPUT_DIR.exists():
        shutil.rmtree(TEST_OUTPUT_DIR)
    TEST_PLACEHOLDER_PATH.write_bytes(PLACEHOLDER_PNG_BYTES)

    with imagegen.get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scripts_generated (
                topic_id, title, script_text, platform, duration_seconds, status, audio_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                "Le dossier impossible du phare abandonné",
                """[HOOK] (0-5s) : Une lumière s'allume chaque nuit dans ce phare officiellement vide.
[DÉVELOPPEMENT] (5-45s) : Les gardiens ont disparu sans laisser de trace. Les registres météo ne mentionnent aucune tempête. Un appel radio a pourtant été capté trois jours plus tard.
[RÉVÉLATION] (45-55s) : Le plus étrange, c'est que la lampe avait été retirée depuis 1978.
[CTA] (55-60s) : Alors, erreur d'archive ou témoin impossible ?""",
                "youtube_shorts_tiktok",
                60,
                "voiced",
                "output/audio/1_le-dossier-impossible-du-phare-abandonne.mp3",
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def fetch_result(script_id: int) -> dict[str, Any]:
    """Return the updated script row after Imagegen execution."""
    with imagegen.get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, title, status, images_path
            FROM scripts_generated
            WHERE id = ?
            """,
            (script_id,),
        ).fetchone()
    return dict(row) if row is not None else {}


def assert_expected_results(
    summary: imagegen.RunSummary,
    result: dict[str, Any],
    client: MockReplicateFluxClient,
) -> list[str]:
    """Validate that four mock image paths were stored in SQLite."""
    if not summary["ok"]:
        raise AssertionError(f"Agent summary is not ok: {summary}")
    if summary["voiced_found"] != 1 or summary["images_created"] != 4:
        raise AssertionError(f"Unexpected summary counts: {summary}")
    if result.get("status") != "illustrated":
        raise AssertionError(f"Expected status illustrated, got: {result}")
    raw_images_path = result.get("images_path")
    if not raw_images_path:
        raise AssertionError(f"images_path was not updated: {result}")
    image_paths = json.loads(raw_images_path)
    if len(image_paths) != 4:
        raise AssertionError(f"Expected four image paths, got: {image_paths}")
    for image_path in image_paths:
        resolved = imagegen.REPO_ROOT / image_path if not Path(image_path).is_absolute() else Path(image_path)
        if not resolved.exists():
            resolved = Path(image_path)
        if not resolved.exists():
            raise AssertionError(f"Expected image file to exist: {image_path}")
    if len(client.calls) != 4:
        raise AssertionError(f"Expected four mock Replicate calls, got {len(client.calls)}")
    for call in client.calls:
        prompt = call["prompt"]
        if "cinematic, dark atmosphere" not in prompt or "no text, no watermark" not in prompt:
            raise AssertionError(f"Prompt does not include required style suffix: {prompt}")
    return image_paths


def main() -> int:
    """Run Imagegen with one fake voiced script and a mock Replicate client."""
    imagegen.configure_logging()
    script_id = reset_test_environment()
    client = MockReplicateFluxClient(TEST_PLACEHOLDER_PATH)

    summary = imagegen.run_agent(client=client, limit=2)
    result = fetch_result(script_id)
    image_paths = assert_expected_results(summary, result, client)

    print(f"Base SQLite temporaire: {TEST_DB_PATH}")
    print(f"Dossier images temporaire: {TEST_OUTPUT_DIR}")
    print(f"Placeholder PNG local: {TEST_PLACEHOLDER_PATH}")
    print("\nRésumé agent:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nScript mis à jour:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\nChemins images JSON:")
    print(json.dumps(image_paths, ensure_ascii=False, indent=2))
    print("\nPrompts envoyés au mock Replicate:")
    for call in client.calls:
        print(f"- {call['prompt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
