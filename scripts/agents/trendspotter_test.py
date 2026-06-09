"""Standalone local smoke test for the DualMind Trendspotter agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import trendspotter


def build_fake_payload() -> dict[str, object]:
    """Return ten deterministic Grok-like trends for local testing."""
    return {
        "source": "grok_weekly_trends_test",
        "generated_at": "2026-06-09T00:00:00Z",
        "trends": [
            {
                "title": "Disparition inexpliquée de 3 randonneurs dans les Alpes",
                "summary": "Une affaire mystérieuse relancée par un nouveau témoin.",
                "url": "https://example.com/trends/alpes-3-randonneurs",
            },
            {
                "title": "Cold case: le suspect oublié revient après 27 ans",
                "summary": "La police rouvre un dossier criminel non classé.",
                "url": "https://example.com/trends/cold-case-27-ans",
            },
            {
                "title": "Un signal étrange capté 6 fois près d'une base abandonnée",
                "summary": "Les archives locales parlent d'un phénomène inexpliqué.",
                "url": "https://example.com/trends/signal-6-fois",
            },
            {
                "title": "Meurtre sans mobile: l'énigme du carnet retrouvé en 1984",
                "summary": "Un détail secret pourrait changer l'enquête.",
                "url": "https://example.com/trends/carnet-1984",
            },
            {
                "title": "OVNI ou drone militaire? 12 témoins décrivent la même scène",
                "summary": "Une affaire étrange agite les réseaux francophones.",
                "url": "https://example.com/trends/ovni-12-temoins",
            },
            {
                "title": "Nouvelle technologie de batterie annoncée en Europe",
                "summary": "Sujet tech généraliste sans angle mystère.",
                "url": "https://example.com/trends/batterie-europe",
            },
            {
                "title": "Recette virale: 5 astuces pour réussir un gâteau au citron",
                "summary": "Tendance cuisine de la semaine.",
                "url": "https://example.com/trends/gateau-citron",
            },
            {
                "title": "Archive oubliée: une victime inconnue identifiée après 41 ans",
                "summary": "L'affaire criminelle pourrait enfin être résolue.",
                "url": "https://example.com/trends/victime-41-ans",
            },
            {
                "title": "Pourquoi cette maison abandonnée attire les enquêteurs?",
                "summary": "Plusieurs disparitions anciennes restent non classées.",
                "url": "https://example.com/trends/maison-abandonnee",
            },
            {
                "title": "Classement sportif hebdomadaire des clubs français",
                "summary": "Résumé sport sans lien avec la niche.",
                "url": "https://example.com/trends/sport-clubs",
            },
        ],
    }


def main() -> int:
    """Run the agent with fake data and print resulting pending hot topics."""
    trendspotter.configure_logging()
    payload = build_fake_payload()
    print("Payload de test:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    summary = trendspotter.run_agent(payload)
    print("\nRésumé agent:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    with trendspotter.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT topic, viral_score, status, created_at
            FROM topics_hot
            WHERE niche = ? AND status = 'pending'
            ORDER BY viral_score DESC, id DESC
            LIMIT 5
            """,
            (trendspotter.NICHE,),
        ).fetchall()

    print("\nTop topics_hot pending:")
    for index, row in enumerate(rows, start=1):
        print(f"{index}. [{row['viral_score']:.0f}] {row['topic']} — {row['status']} ({row['created_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
