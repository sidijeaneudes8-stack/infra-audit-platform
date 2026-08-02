"""
Suivi commercial (prospection) par agence, géré depuis le dashboard.
Ne touche jamais à audit_status (piloté par le cycle de test) — uniquement
à prospecting_status, qui suit le cycle humain de vente.
"""
import logging
from datetime import datetime, timezone

from lib.db import get_session
from models.schema import Agency, ProspectingStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VALID_ACTIONS = {"appele", "relance", "signee", "perdue", "reset"}

# Au-delà de ce nombre de jours sans mise à jour de statut, le dashboard
# affiche une alerte "relance à faire" (2 relances max, ensuite silence).
RELANCE_DUE_AFTER_DAYS = 5


def apply_prospecting_action(agency_id: str, action: str) -> dict:
    if action not in VALID_ACTIONS:
        return {"error": f"Action inconnue : {action}"}

    with get_session() as session:
        agency = session.query(Agency).filter(Agency.id == agency_id).first()
        if agency is None:
            return {"error": "Agence introuvable."}

        now = datetime.now(timezone.utc)

        if action == "appele":
            agency.prospecting_status = ProspectingStatus.APPELE
            agency.last_contact_at = now

        elif action == "relance":
            if agency.relance_count >= 2:
                return {"error": "2 relances déjà effectuées, aucune relance supplémentaire prévue."}
            agency.relance_count += 1
            agency.prospecting_status = (
                ProspectingStatus.RELANCE_1 if agency.relance_count == 1 else ProspectingStatus.RELANCE_2
            )
            agency.last_contact_at = now

        elif action == "signee":
            agency.prospecting_status = ProspectingStatus.SIGNEE
            agency.last_contact_at = now

        elif action == "perdue":
            agency.prospecting_status = ProspectingStatus.PERDUE
            agency.last_contact_at = now

        elif action == "reset":
            agency.prospecting_status = ProspectingStatus.EN_COURS
            agency.relance_count = 0
            agency.last_contact_at = None

        session.flush()
        return {
            "agency_id": agency.id,
            "prospecting_status": agency.prospecting_status.value,
            "relance_count": agency.relance_count,
        }
