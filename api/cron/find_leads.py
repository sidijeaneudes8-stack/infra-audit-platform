"""
Vercel Serverless Function (cron quotidien, 06:00 UTC).
Sourcing des agences ICP + extraction de 3 biens réels par agence.

Point d'entrée Vercel Python : la fonction `handler(request)` est appelée
directement par le runtime (format BaseHTTPRequestHandler ou WSGI selon
la config vercel.json / runtime python3.11 utilisé).
"""
import logging
import os
import time

import requests
from bs4 import BeautifulSoup

from lib.db import get_session
from lib.places_client import find_agencies
from lib.utils import random_user_agent
from models.schema import Agency, AuditStatus, Property

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ICP_LOCATIONS = [loc.strip() for loc in os.environ.get("ICP_LOCATIONS", "").split(",") if loc]
ICP_MIN_PROPERTIES = int(os.environ.get("ICP_MIN_PROPERTIES", "50"))
ICP_RESULTS_PER_LOCATION = int(os.environ.get("ICP_RESULTS_PER_LOCATION", "5"))
REQUEST_TIMEOUT = 10
DELAY_BETWEEN_REQUESTS = 1.5


def search_agencies(icp_criteria: dict) -> list[dict]:
    """
    Recherche les agences correspondant à l'ICP via l'API officielle Google
    Places (lib/places_client.py) : conforme aux CGU, contrairement au
    scraping direct d'un moteur de recherche. Pour chaque agence trouvée,
    tente d'extraire un email public depuis son propre site (contact/accueil).

    Fallback : si GOOGLE_PLACES_API_KEY n'est pas configuré, retombe sur
    SEED_AGENCIES_JSON (liste pré-qualifiée manuelle) pour ne pas bloquer
    le pipeline pendant la mise en place de la clé API.
    """
    locations = icp_criteria.get("locations") or []

    if os.environ.get("GOOGLE_PLACES_API_KEY"):
        try:
            return find_agencies(locations, min_results_per_location=ICP_RESULTS_PER_LOCATION)
        except Exception as exc:  # noqa: BLE001
            logger.error("Sourcing via Google Places a échoué : %s", exc)

    seed_agencies_raw = os.environ.get("SEED_AGENCIES_JSON")
    if not seed_agencies_raw:
        logger.warning(
            "Ni GOOGLE_PLACES_API_KEY ni SEED_AGENCIES_JSON configurés : "
            "aucune source de leads disponible."
        )
        return []

    import json

    agencies = json.loads(seed_agencies_raw)
    filtered = []
    for agency in agencies:
        if locations and agency.get("location") not in locations:
            continue
        filtered.append(agency)
    return filtered


def extract_properties(agency_url: str) -> list[dict]:
    """
    Visite le catalogue de l'agence et extrait jusqu'à 3 biens réels distincts.

    Les sélecteurs CSS ci-dessous sont génériques et DOIVENT être adaptés
    au HTML réel de chaque site cible (chaque agence a sa propre structure).
    Prévoir un mapping par domaine dans une table de config si le volume grandit.
    """
    headers = {"User-Agent": random_user_agent()}
    try:
        resp = requests.get(agency_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Erreur scraping %s : %s", agency_url, exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Sélecteurs génériques à adapter par site (listing de biens courant sur les
    # sites d'agences immobilières : cartes/vignettes avec lien, titre, référence).
    candidate_cards = soup.select("[class*='property'], [class*='listing'], [class*='bien']")

    properties = []
    for card in candidate_cards:
        if len(properties) >= 3:
            break

        link_tag = card.find("a", href=True)
        title_tag = card.find(["h2", "h3", "h4"])
        ref_tag = card.find(attrs={"class": lambda c: c and "ref" in c.lower()})

        if not link_tag or not title_tag:
            continue

        property_url = link_tag["href"]
        if property_url.startswith("/"):
            base = "/".join(agency_url.split("/")[:3])
            property_url = base + property_url

        properties.append(
            {
                "property_ref": ref_tag.get_text(strip=True) if ref_tag else f"REF-{len(properties)+1}",
                "property_title": title_tag.get_text(strip=True),
                "property_url": property_url,
                "property_price": None,
            }
        )

    time.sleep(DELAY_BETWEEN_REQUESTS)
    return properties


def run() -> dict:
    icp_criteria = {"locations": ICP_LOCATIONS, "min_properties": ICP_MIN_PROPERTIES}
    found_agencies = search_agencies(icp_criteria)

    created = 0
    with get_session() as session:
        for agency_data in found_agencies:
            existing = (
                session.query(Agency)
                .filter(Agency.public_email == agency_data["public_email"])
                .first()
            )
            if existing:
                continue

            properties = extract_properties(agency_data.get("catalog_url", ""))
            if len(properties) < 1:
                logger.warning("Aucun bien extrait pour %s, agence ignorée.", agency_data.get("name"))
                continue

            agency = Agency(
                name=agency_data["name"],
                public_email=agency_data["public_email"],
                catalog_url=agency_data.get("catalog_url"),
                audit_status=AuditStatus.PENDING,
            )
            session.add(agency)
            session.flush()  # obtenir agency.id avant de créer les Property liées

            # On persiste jusqu'à 3 biens, un par test_index (1, 2, 3), pour
            # que send_tests.py puisse les consommer plus tard sans re-scraper.
            for i, prop in enumerate(properties[:3], start=1):
                session.add(
                    Property(
                        agency_id=agency.id,
                        test_index=i,
                        property_ref=prop.get("property_ref"),
                        property_title=prop.get("property_title"),
                        property_url=prop.get("property_url"),
                        property_price=prop.get("property_price"),
                    )
                )

            created += 1

    logger.info("find_leads terminé : %d nouvelles agences ajoutées.", created)
    return {"agencies_created": created}


# Note : le routage HTTP est géré par api/index.py (point d'entrée unique
# FastAPI exigé par le runtime Python Vercel), qui appelle run() ci-dessus.
