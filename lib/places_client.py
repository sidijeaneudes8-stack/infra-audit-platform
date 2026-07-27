"""
Client Google Places API (New / Legacy Text Search + Place Details).
Remplace le scraping direct de moteurs de recherche par une source de
données officielle et conforme aux CGU pour le sourcing des agences ICP.

Nécessite GOOGLE_PLACES_API_KEY (API "Places API" activée sur le projet
Google Cloud, facturation activée — un quota gratuit mensuel existe).
"""
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")
TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
REQUEST_TIMEOUT = 10

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _require_api_key() -> str:
    if not PLACES_API_KEY:
        raise RuntimeError(
            "GOOGLE_PLACES_API_KEY manquant. Active l'API Places sur "
            "console.cloud.google.com et ajoute la clé en variable d'env."
        )
    return PLACES_API_KEY


def text_search(query: str) -> list[dict]:
    """Recherche de lieux par requête libre (ex: 'agence immobilière Paris')."""
    api_key = _require_api_key()
    params = {"query": query, "key": api_key, "type": "real_estate_agency"}

    try:
        resp = requests.get(TEXT_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Erreur Google Places text_search('%s'): %s", query, exc)
        return []

    data = resp.json()
    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        logger.error("Google Places status=%s pour '%s': %s", status, query, data.get("error_message"))
        return []

    return data.get("results", [])


def place_details(place_id: str) -> dict:
    """Récupère website, téléphone, adresse formatée pour un place_id donné."""
    api_key = _require_api_key()
    params = {
        "place_id": place_id,
        "key": api_key,
        "fields": "name,website,formatted_phone_number,formatted_address,url",
    }

    try:
        resp = requests.get(DETAILS_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Erreur Google Places details(%s): %s", place_id, exc)
        return {}

    data = resp.json()
    if data.get("status") != "OK":
        return {}
    return data.get("result", {})


def extract_public_email(website_url: str) -> str | None:
    """
    Best-effort : va chercher un email de contact sur la page d'accueil et,
    si absent, sur une page 'contact' probable du site de l'agence.
    On scrape ici le site PROPRE de l'agence (pas un moteur de recherche
    tiers), ce qui est nettement plus défendable en termes de CGU — c'est
    l'équivalent de visiter le site pour trouver son adresse de contact.
    """
    if not website_url:
        return None

    from lib.utils import random_user_agent

    headers = {"User-Agent": random_user_agent()}
    candidate_urls = [website_url]
    for suffix in ("/contact", "/contact-us", "/nous-contacter"):
        candidate_urls.append(website_url.rstrip("/") + suffix)

    for url in candidate_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException:
            continue

        match = EMAIL_RE.search(resp.text)
        if match:
            return match.group(0)

    return None


def find_agencies(locations: list[str], min_results_per_location: int = 5) -> list[dict]:
    """
    Point d'entrée principal du sourcing : pour chaque localisation ICP,
    cherche des agences immobilières via Places API, récupère leurs
    coordonnées (site web) puis tente d'en extraire un email public.

    Retourne une liste de dicts : name, public_email, catalog_url, location.
    Les agences sans email détecté sont exclues (impossibles à auditer).
    """
    results = []

    for location in locations:
        places = text_search(f"agence immobilière {location}")[:min_results_per_location]

        for place in places:
            details = place_details(place.get("place_id", ""))
            website = details.get("website")
            if not website:
                continue

            email_addr = extract_public_email(website)
            if not email_addr:
                logger.info("Pas d'email trouvé pour %s (%s), agence exclue.", place.get("name"), website)
                continue

            results.append(
                {
                    "name": details.get("name") or place.get("name"),
                    "public_email": email_addr,
                    "catalog_url": website,
                    "location": location,
                }
            )

    return results
