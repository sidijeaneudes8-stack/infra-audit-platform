"""
Couche métier reliant classification Groq et calcul de latence.
Séparée de groq_client.py pour isoler la logique métier de l'appel API brut.
"""
from datetime import datetime, timezone

from lib.groq_client import classify_as_human


def classify_response(response_text: str) -> bool:
    """Retourne True si la réponse est jugée humaine par le LLM."""
    return classify_as_human(response_text)


def calculate_latency(sent_at: datetime, received_at: datetime) -> int:
    """Latence en minutes entre l'envoi du test et la réception de la réponse."""
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    delta = received_at - sent_at
    return max(0, int(delta.total_seconds() // 60))


def is_expired(sent_at: datetime, now: datetime | None = None, hours: int = 72) -> bool:
    """True si plus de `hours` heures se sont écoulées depuis l'envoi sans réponse."""
    now = now or datetime.now(timezone.utc)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return (now - sent_at).total_seconds() / 3600 >= hours
