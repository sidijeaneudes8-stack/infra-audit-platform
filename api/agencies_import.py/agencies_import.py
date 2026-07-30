"""
Import manuel d'agences (+ leurs biens) directement en base, sans passer par
le scraping automatique (fragile car sélecteurs génériques par site).

Alimente le même pipeline que find_leads.py : les agences créées ici sont
ensuite reprises normalement par send_tests.py selon le planning échelonné.
"""
import logging

from lib.db import get_session
from models.schema import Agency, AuditStatus, Property

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def import_agencies(payload: list[dict]) -> dict:
    created = 0
    skipped = 0
    errors = []

    with get_session() as session:
        for i, item in enumerate(payload):
            name = (item.get("name") or "").strip()
            email = (item.get("public_email") or "").strip()

            if not name or not email:
                errors.append(f"Élément {i} : 'name' et 'public_email' sont obligatoires.")
                continue

            existing = session.query(Agency).filter(Agency.public_email == email).first()
            if existing:
                skipped += 1
                continue

            agency = Agency(
                name=name,
                public_email=email,
                catalog_url=item.get("catalog_url"),
                audit_status=AuditStatus.PENDING,
            )
            session.add(agency)
            session.flush()

            properties = item.get("properties") or []
            for idx, prop in enumerate(properties[:3], start=1):
                title = (prop.get("property_title") or "").strip()
                if not title:
                    continue
                session.add(
                    Property(
                        agency_id=agency.id,
                        test_index=idx,
                        property_ref=prop.get("property_ref") or f"REF-{idx}",
                        property_title=title,
                        property_url=prop.get("property_url"),
                        property_price=prop.get("property_price"),
                    )
                )

            created += 1

    logger.info("Import terminé : %d créées, %d doublons ignorés.", created, skipped)
    return {"agencies_created": created, "duplicates_skipped": skipped, "errors": errors}
