"""
Vercel Serverless Function (cron toutes les 2h).
Écoute les 3 boîtes de réception, filtre les auto-réponses (gratuit),
classifie le reste via Groq (is_human), calcule la latence, et marque
les tests sans réponse après 72h comme IGNORED.
"""
import logging
import os
from datetime import datetime, timezone

from lib.ai_validator import calculate_latency, classify_response, is_expired
from lib.db import get_session
from lib.email_parser import (
    connect_imap,
    fetch_unseen_messages,
    filter_auto_replies,
    get_body_text,
    get_received_at,
)
from models.schema import Agency, Audit, AuditStatus, ProspectingStatus, TestStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))


def _match_audit(session, to_sender_email: str, from_agency_email: str) -> Audit | None:
    """Retrouve l'audit correspondant à une réponse reçue, à partir de la
    boîte qui a reçu la réponse (= l'adresse émettrice du test) et de
    l'expéditeur de la réponse (= l'email de l'agence)."""
    return (
        session.query(Audit)
        .filter(
            Audit.sender_email == to_sender_email,
            Audit.test_status == TestStatus.SENT,
        )
        .join(Audit.agency)
        .filter_by(public_email=from_agency_email)
        .order_by(Audit.sent_at.desc())
        .first()
    )


def _process_mailbox(session, index: int) -> dict:
    email_addr = os.environ.get(f"SENDER_EMAIL_{index}")
    password = os.environ.get(f"SENDER_PASSWORD_{index}")
    if not email_addr or not password:
        logger.warning("Boîte %d non configurée, ignorée.", index)
        return {"processed": 0, "auto": 0, "human": 0}

    stats = {"processed": 0, "auto": 0, "human": 0}

    try:
        conn = connect_imap(IMAP_HOST, IMAP_PORT, email_addr, password)
    except Exception as exc:  # noqa: BLE001
        logger.error("Connexion IMAP échouée pour %s : %s", email_addr, exc)
        return stats

    try:
        messages = fetch_unseen_messages(conn)
    finally:
        conn.logout()

    for msg in messages:
        stats["processed"] += 1
        from_addr = _extract_from(msg)

        audit = _match_audit(session, email_addr, from_addr)
        if not audit:
            continue

        # Étape 1 : filtrage déterministe, zéro coût API
        if filter_auto_replies(msg):
            audit.test_status = TestStatus.RESPONDED_AUTO
            audit.received_at = get_received_at(msg)
            audit.is_human_response = False
            stats["auto"] += 1
            continue

        # Étape 2 : classification Groq
        body = get_body_text(msg)
        received_at = get_received_at(msg)
        is_human = classify_response(body)

        audit.response_text = body
        audit.received_at = received_at
        audit.is_human_response = is_human

        if is_human:
            audit.test_status = TestStatus.RESPONDED_HUMAN
            audit.latency_minutes = calculate_latency(audit.sent_at, received_at)
            stats["human"] += 1
        else:
            audit.test_status = TestStatus.RESPONDED_AUTO
            stats["auto"] += 1

    return stats


def _extract_from(msg) -> str:
    import email.utils

    return email.utils.parseaddr(msg.get("From", ""))[1]


def _expire_stale_tests(session) -> int:
    now = datetime.now(timezone.utc)
    pending = session.query(Audit).filter(Audit.test_status == TestStatus.SENT).all()

    expired = 0
    for audit in pending:
        if audit.sent_at and is_expired(audit.sent_at, now=now, hours=72):
            audit.test_status = TestStatus.IGNORED
            audit.latency_minutes = None
            expired += 1
    return expired


def _finalize_completed_agencies(session) -> int:
    """Passe une agence en COMPLETED dès que son test 3 a une issue connue
    (répondu ou expiré), et déclenche automatiquement la prospection en la
    faisant passer en 'EN_COURS' si elle n'a pas encore été contactée.
    Corrige un bug où audit_status restait bloqué sur IN_PROGRESS à vie."""
    agencies = (
        session.query(Agency)
        .filter(Agency.audit_status == AuditStatus.IN_PROGRESS)
        .all()
    )

    finalized = 0
    for agency in agencies:
        test3 = (
            session.query(Audit)
            .filter(Audit.agency_id == agency.id, Audit.test_index == 3)
            .first()
        )
        if test3 is None or test3.test_status == TestStatus.SENT:
            continue  # test 3 pas encore envoyé ou toujours en attente de réponse

        agency.audit_status = AuditStatus.COMPLETED
        if agency.prospecting_status == ProspectingStatus.NON_CONTACTEE:
            agency.prospecting_status = ProspectingStatus.EN_COURS
        finalized += 1

    return finalized


def run() -> dict:
    total_stats = {"processed": 0, "auto": 0, "human": 0, "expired": 0, "finalized": 0}

    with get_session() as session:
        for index in (1, 2, 3):
            stats = _process_mailbox(session, index)
            total_stats["processed"] += stats["processed"]
            total_stats["auto"] += stats["auto"]
            total_stats["human"] += stats["human"]

        total_stats["expired"] = _expire_stale_tests(session)
        total_stats["finalized"] = _finalize_completed_agencies(session)

    logger.info("check_inbox terminé : %s", total_stats)
    return total_stats


# Note : le routage HTTP est géré par api/index.py (point d'entrée unique
# FastAPI exigé par le runtime Python Vercel), qui appelle run() ci-dessus.
