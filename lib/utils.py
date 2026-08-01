"""
Utilitaires génériques : rotation user-agents, planification échelonnée
des 3 tests, envoi SMTP.
"""
import logging
import os
import random
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def _next_business_day(d: datetime) -> datetime:
    """Avance la date au prochain jour ouvré si elle tombe un week-end
    (samedi -> lundi, dimanche -> lundi). Un agent immobilier n'est pas
    testé sur sa réactivité hors jours ouvrés."""
    while d.weekday() >= 5:  # 5 = samedi, 6 = dimanche
        d += timedelta(days=1)
    return d


def compute_test_schedule(base_date: datetime | None = None) -> list[datetime]:
    """Retourne les 3 dates d'envoi planifiées, sur 3 jours ouvrés proches
    (J+1, J+2, J+3 par défaut), en sautant les week-ends. Un agent
    immobilier haut de gamme doit rester réactif sur une semaine de
    travail normale : un écart de plusieurs jours entre les tests
    n'aurait pas de sens pour ce protocole."""
    base_date = base_date or datetime.now(timezone.utc)
    dates = []
    current = base_date
    for _ in range(3):
        current = _next_business_day(current + timedelta(days=1))
        dates.append(current)
    return dates


def get_sender_credentials(index: int) -> tuple[str, str]:
    """index: 1, 2 ou 3. Retourne (email, password) depuis les variables d'env."""
    email_addr = os.environ.get(f"SENDER_EMAIL_{index}")
    password = os.environ.get(f"SENDER_PASSWORD_{index}")
    if not email_addr or not password:
        raise RuntimeError(f"Credentials manquants pour SENDER_EMAIL_{index}")
    return email_addr, password


def send_email_smtp(
    sender_email: str,
    sender_password: str,
    to_email: str,
    subject: str,
    body: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> None:
    """Envoie un email via SMTP (STARTTLS)."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [to_email], msg.as_string())

    logger.info("Email envoyé de %s vers %s", sender_email, to_email)
