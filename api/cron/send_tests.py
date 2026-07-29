"""
Vercel Serverless Function (cron quotidien, 07:00 UTC).
Envoie les demandes d'information dues aujourd'hui, en respectant le
planning échelonné (jours non successifs) et la rotation des 3 adresses
émettrices.
"""
import logging
from datetime import datetime, timezone

from lib.db import get_session
from lib.groq_client import generate_inquiry
from lib.utils import compute_test_schedule, get_sender_credentials, send_email_smtp
from models.schema import Agency, Audit, AuditStatus, Property, TestStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_AGENCIES_PER_RUN = 20  # borne pour rester < 30s d'exécution serverless


def _property_for_test(session, agency: Agency, test_index: int) -> dict | None:
    """
    Sélectionne le bien réel (extrait lors du sourcing find_leads et
    persisté en base) à utiliser pour le test N. Retourne None si aucun
    bien n'a été trouvé pour cet index — dans ce cas le test est reporté
    plutôt que d'envoyer un email générique sur un faux bien.
    """
    prop = (
        session.query(Property)
        .filter(
            Property.agency_id == agency.id,
            Property.test_index == test_index,
            Property.used.is_(False),
        )
        .first()
    )
    if not prop:
        return None

    return {
        "property_ref": prop.property_ref or f"REF-{test_index}",
        "property_title": prop.property_title,
        "property_url": prop.property_url or agency.catalog_url or "",
        "_row": prop,
    }


def _due_test_index(agency: Agency, existing_tests: list[Audit], now: datetime) -> int | None:
    """Détermine quel test (1, 2 ou 3) est dû aujourd'hui, selon le planning
    échelonné calculé depuis la création de l'agence."""
    schedule = compute_test_schedule(base_date=agency.created_at)
    sent_indices = {a.test_index for a in existing_tests}

    for i, planned_date in enumerate(schedule, start=1):
        if i in sent_indices:
            continue
        if planned_date.date() <= now.date():
            return i
    return None


def run() -> dict:
    now = datetime.now(timezone.utc)
    sent_count = 0

    with get_session() as session:
        agencies = (
            session.query(Agency)
            .filter(Agency.audit_status.in_([AuditStatus.PENDING, AuditStatus.IN_PROGRESS]))
            .limit(MAX_AGENCIES_PER_RUN)
            .all()
        )

        for agency in agencies:
            existing_tests = session.query(Audit).filter(Audit.agency_id == agency.id).all()
            test_index = _due_test_index(agency, existing_tests, now)

            if test_index is None:
                continue

            prop = _property_for_test(session, agency, test_index)
            if prop is None:
                logger.warning(
                    "Aucun bien disponible pour %s (test %d), test reporté.",
                    agency.name,
                    test_index,
                )
                continue

            inquiry_text = generate_inquiry(
                prop["property_ref"], prop["property_title"], prop["property_url"]
            )

            sender_email, sender_password = get_sender_credentials(test_index)

            try:
                send_email_smtp(
                    sender_email=sender_email,
                    sender_password=sender_password,
                    to_email=agency.public_email,
                    subject=f"Demande d'information - {prop['property_title']}",
                    body=inquiry_text,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Échec envoi pour %s (test %d): %s", agency.name, test_index, exc)
                continue

            audit = Audit(
                agency_id=agency.id,
                test_index=test_index,
                property_ref=prop["property_ref"],
                property_title=prop["property_title"],
                property_url=prop["property_url"],
                sender_email=sender_email,
                inquiry_text=inquiry_text,
                sent_at=now,
                test_status=TestStatus.SENT,
            )
            session.add(audit)
            prop["_row"].used = True

            agency.audit_status = AuditStatus.IN_PROGRESS
            sent_count += 1

    logger.info("send_tests terminé : %d emails envoyés.", sent_count)
    return {"tests_sent": sent_count}


# Note : le routage HTTP est géré par api/index.py (point d'entrée unique
# FastAPI exigé par le runtime Python Vercel), qui appelle run() ci-dessus.
