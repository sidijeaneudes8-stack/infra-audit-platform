"""
Vercel Serverless Function (cron quotidien, 08:00 UTC = 9h Cotonou).
Envoie les demandes d'information dues aujourd'hui, en respectant le
planning échelonné (lundi/mercredi/vendredi) et la rotation des 3 adresses
émettrices.

Expose aussi send_now_exception() : un déclenchement manuel ponctuel qui
envoie le test 1 immédiatement, sans attendre le prochain jour planifié —
prévu pour les cas où le lot hebdomadaire n'a pas pu être préparé à temps.
"""
import logging
import os
from datetime import datetime, timezone

from lib.db import get_session
from lib.groq_client import generate_inquiry
from lib.utils import compute_test_schedule, get_sender_credentials, send_email_smtp
from models.schema import Agency, Audit, AuditStatus, Property, TestStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_AGENCIES_PER_RUN = 20  # borne pour rester < 30s d'exécution serverless

# Plafond de NOUVEAUX premiers contacts (test 1) envoyés par jour. Le
# protocole vise un lot hebdomadaire (15-20 agences) démarré le même jour,
# donc le plafond par défaut couvre un lot complet plutôt que de l'étaler
# artificiellement. S'applique aussi à send_now_exception().
NEW_AGENCY_DAILY_CAP = int(os.environ.get("NEW_AGENCY_DAILY_CAP", "20"))


def _property_for_test(session, agency: Agency, test_index: int) -> dict | None:
    """
    Sélectionne le bien réel (fourni à l'import ou extrait par find_leads)
    à utiliser pour le test N. Retourne None si aucun bien n'a été trouvé
    pour cet index — dans ce cas le test est reporté plutôt que d'envoyer
    un email générique sur un faux bien.
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


def _send_one_test(session, agency: Agency, test_index: int, now: datetime) -> bool:
    """Envoie réellement le test N pour une agence, et enregistre l'Audit
    correspondant. Retourne True si l'envoi a réussi."""
    prop = _property_for_test(session, agency, test_index)
    if prop is None:
        logger.warning(
            "Aucun bien disponible pour %s (test %d), test reporté.",
            agency.name,
            test_index,
        )
        return False

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
        return False

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
    return True


def run() -> dict:
    """Exécution normale du cron : envoie tout ce qui est dû aujourd'hui
    selon le planning lundi/mercredi/vendredi."""
    now = datetime.now(timezone.utc)
    sent_count = 0
    new_contacts_today = 0

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

            if test_index == 1 and new_contacts_today >= NEW_AGENCY_DAILY_CAP:
                # Plafond quotidien atteint : cette agence sera reprise
                # automatiquement au prochain run (sa date planifiée est
                # déjà passée, elle reste éligible).
                continue

            sent = _send_one_test(session, agency, test_index, now)
            if sent:
                sent_count += 1
                if test_index == 1:
                    new_contacts_today += 1

    logger.info(
        "send_tests terminé : %d emails envoyés (%d nouveaux contacts).",
        sent_count,
        new_contacts_today,
    )
    return {"tests_sent": sent_count, "new_contacts": new_contacts_today}


def send_now_exception(agency_ids: list[str] | None = None) -> dict:
    """Déclenchement manuel ponctuel depuis le dashboard : envoie le test 1
    immédiatement pour les agences indiquées (ou, si non précisé, toutes
    les agences PENDING qui n'ont encore reçu aucun test), sans attendre
    le prochain lundi/mercredi/vendredi. Respecte toujours le plafond
    quotidien de nouveaux contacts, pour ne jamais dépasser le volume
    prévu même en mode exceptionnel."""
    now = datetime.now(timezone.utc)
    sent_count = 0
    skipped_cap = 0

    with get_session() as session:
        query = session.query(Agency).filter(Agency.audit_status == AuditStatus.PENDING)
        if agency_ids:
            query = query.filter(Agency.id.in_(agency_ids))
        agencies = query.limit(MAX_AGENCIES_PER_RUN).all()

        for agency in agencies:
            already_sent = session.query(Audit).filter(Audit.agency_id == agency.id).count()
            if already_sent > 0:
                continue  # déjà démarrée, pas concernée par l'exception

            if sent_count >= NEW_AGENCY_DAILY_CAP:
                skipped_cap += 1
                continue

            if _send_one_test(session, agency, 1, now):
                sent_count += 1

    logger.info(
        "send_now_exception terminé : %d tests 1 envoyés immédiatement (%d bloqués par le plafond).",
        sent_count,
        skipped_cap,
    )
    return {"tests_sent_now": sent_count, "skipped_by_cap": skipped_cap}


# Note : le routage HTTP est géré par api/index.py (point d'entrée unique
# FastAPI exigé par le runtime Python Vercel), qui appelle run() ci-dessus.
