"""
Parsing IMAP + filtrage déterministe des auto-réponses.
Aucune dépendance à Groq ici : cette étape doit être 100% gratuite.
"""
import email
import imaplib
import logging
import re
from email.message import Message
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

AUTO_REPLY_HEADERS = {
    "auto-submitted": lambda v: v.lower() != "no",
    "x-autoreply": lambda v: v.lower() == "yes",
    "precedence": lambda v: v.lower() in ("auto_reply", "bulk", "junk"),
    "x-autorespond": lambda v: True,
}

SUBJECT_AUTO_PATTERNS = [
    r"\bre\s*:\s*absence\b",
    r"\babsence du bureau\b",
    r"\bauto[- ]r[ée]ponse\b",
    r"\bout of office\b",
    r"\bautomatic reply\b",
    r"\breponse automatique\b",
    r"\bcong[ée]s?\b",
    r"\bindisponible\b",
]

_SUBJECT_RE = re.compile("|".join(SUBJECT_AUTO_PATTERNS), re.IGNORECASE)


def connect_imap(host: str, port: int, user: str, password: str, timeout: int = 15) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    conn.login(user, password)
    return conn


def fetch_recent_messages(conn: imaplib.IMAP4_SSL, mailbox: str = "INBOX", days: int = 10):
    """Récupère les messages des N derniers jours, qu'ils aient été lus ou
    non. Ouvrir un email pour le consulter le marque automatiquement comme
    "lu" côté Gmail — se baser sur UNSEEN le rendrait invisible pour
    toujours. La déduplication (ne pas retraiter un audit déjà résolu) est
    gérée en base, pas via le statut lu/non lu de la boîte mail."""
    conn.select(mailbox)
    since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    status, data = conn.search(None, f'(SINCE "{since_date}")')
    if status != "OK":
        return []

    messages = []
    for num in data[0].split():
        status, msg_data = conn.fetch(num, "(RFC822)")
        if status != "OK":
            continue
        raw = msg_data[0][1]
        messages.append(email.message_from_bytes(raw))
    return messages


def get_body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:  # noqa: BLE001
        return ""


def get_received_at(msg: Message) -> datetime:
    date_header = msg.get("Date")
    if date_header:
        try:
            return parsedate_to_datetime(date_header)
        except Exception:  # noqa: BLE001
            pass
    return datetime.utcnow()


def filter_auto_replies(msg: Message) -> bool:
    """Retourne True si le message est détecté comme une réponse automatique.
    Purement déterministe (en-têtes + regex sujet) : zéro appel API."""
    for header, check in AUTO_REPLY_HEADERS.items():
        value = msg.get(header)
        if value and check(value):
            logger.info("Auto-réponse détectée via en-tête '%s: %s'", header, value)
            return True

    subject = msg.get("Subject", "") or ""
    if _SUBJECT_RE.search(subject):
        logger.info("Auto-réponse détectée via sujet: %s", subject)
        return True

    return False
