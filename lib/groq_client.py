"""
Wrapper autour de l'API Groq (llama-3.1-8b-instant, free tier).
Toutes les fonctions renvoient un JSON déjà parsé et gèrent le
backoff exponentiel sur les erreurs 429 (rate limit) via tenacity.
"""
import json
import logging
import os

from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY manquant.")
        _client = Groq(api_key=api_key)
    return _client


class GroqRateLimitError(Exception):
    pass


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(GroqRateLimitError),
    reraise=True,
)
def _call_groq(prompt: str, system: str | None = None, max_tokens: int = 300) -> str:
    client = _get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.4,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit(exc):
            logger.warning("Groq 429 reçu, backoff en cours...")
            raise GroqRateLimitError(str(exc)) from exc
        raise

    return completion.choices[0].message.content


def _safe_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Réponse Groq non-JSON: %s", raw[:200])
        return {}


def generate_inquiry(property_ref: str, property_title: str, property_url: str) -> str:
    """Génère une demande d'information naturelle citant le bien concerné."""
    system = (
        "Tu es un particulier intéressé par un bien immobilier. "
        "Réponds UNIQUEMENT en JSON valide au format "
        '{"inquiry_text": "..."}. Le texte doit être naturel, court (3-5 phrases), '
        "en français, poli, et doit citer explicitement le titre et la référence du bien."
    )
    prompt = (
        f"Bien : {property_title}\n"
        f"Référence : {property_ref}\n"
        f"URL : {property_url}\n\n"
        "Rédige une demande d'information pour ce bien."
    )
    raw = _call_groq(prompt, system=system, max_tokens=250)
    data = _safe_json(raw)
    return data.get(
        "inquiry_text",
        f"Bonjour, je suis intéressé(e) par le bien {property_title} "
        f"(réf. {property_ref}). Pourriez-vous me donner plus d'informations ? Merci.",
    )


def classify_as_human(response_text: str) -> bool:
    """Classifie un email de réponse comme provenant d'un humain ou d'un répondeur automatique."""
    prompt = (
        "Analyse cet email. Est-ce une réponse humaine réelle ?\n"
        'Réponds UNIQUEMENT en JSON : {"is_human": true/false}\n\n'
        f"Email:\n{response_text[:2000]}"
    )
    raw = _call_groq(prompt, max_tokens=50)
    data = _safe_json(raw)
    return bool(data.get("is_human", False))
