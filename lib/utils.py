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


def compute_test_schedule(base_date: datetime | None = None) -> list[datetime]:
    """Retourne les 3 dates fixes de la semaine : lundi / mercredi / vendredi.

    Le calendrier est ancré sur le LUNDI de la semaine d'import, pas sur un
    décompte glissant depuis la date d'import :
    - Import lundi/mardi/mercredi/jeudi/vendredi -> semaine EN COURS
      (le lundi déjà passé reste la référence ; utile avec le bouton
      "Envoyer maintenant" quand l'import a lieu après 9h lundi).
    - Import samedi/dimanche -> semaine SUIVANTE (on ne revient jamais en
      arrière sur un lundi déjà terminé).

    Peu importe quand le test 1 part réellement (planning normal ou
    déclenchement manuel exceptionnel), test 2 et test 3 restent fixés au
    mercredi et au vendredi de cette même semaine — jamais recalculés
    depuis l'heure réelle d'envoi du test 1."""
    base_date = base_date or datetime.now(timezone.utc)

    days_since_monday = base_date.weekday()  # lundi=0 ... dimanche=6
    if base_date.weekday() >= 5:  # samedi ou dimanche -> semaine suivante
        monday = base_date - timedelta(days=days_since_monday) + timedelta(days=7)
    else:
        monday = base_date - timedelta(days=days_since_monday)

    return [monday, monday + timedelta(days=2), monday + timedelta(days=4)]


def get_sender_credentials(index: int) -> tuple[str, str]:
    """index: 1, 2 ou 3. Retourne (email, password) depuis les variables d'env."""
    email_addr = os.environ.get(f"SENDER_EMAIL_{index}")
    password = os.environ.get(f"SENDER_PASSWORD_{index}")
    if not email_addr or not password:
        raise RuntimeError(f"Credentials manquants pour SENDER_EMAIL_{index}")
    return email_addr.strip(), password.strip()


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
