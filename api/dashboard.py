"""
Vercel Serverless Function : GET /api/dashboard
Retourne les métriques agrégées d'audit. Ajoute ?format=csv pour un export
CSV téléchargeable (rapport détaillé, usage interne / import Notion, Excel...).
"""
import csv
import io
import json
from datetime import datetime, timezone

from lib.db import get_session
from models.schema import Agency, Audit, ProspectingStatus, TestStatus

RELANCE_DUE_AFTER_DAYS = 5


def _quality_label(audit: Audit) -> str:
    """Indice de qualité de la réponse, calibré pour du standing luxe :
    un client haut de gamme attend une réponse humaine rapide."""
    if audit.test_status == TestStatus.IGNORED:
        return "Ignoré"
    if audit.test_status == TestStatus.RESPONDED_AUTO:
        return "Auto-réponse"
    if audit.test_status == TestStatus.SENT:
        return "En attente"
    if audit.test_status == TestStatus.RESPONDED_HUMAN:
        latency = audit.latency_minutes
        if latency is None:
            return "Répondu"
        if latency <= 30:
            return "Excellent"
        if latency <= 120:
            return "Bon"
        if latency <= 1440:
            return "Moyen"
        return "Lent"
    return "—"


def _build_metrics(session) -> dict:
    agencies = session.query(Agency).all()
    audits = session.query(Audit).order_by(Audit.sent_at.desc()).all()
    agency_by_id = {a.id: a for a in agencies}

    total_tests = len(audits)
    human_responses = [a for a in audits if a.test_status == TestStatus.RESPONDED_HUMAN]
    auto_responses = [a for a in audits if a.test_status == TestStatus.RESPONDED_AUTO]
    ignored = [a for a in audits if a.test_status == TestStatus.IGNORED]
    pending = [a for a in audits if a.test_status == TestStatus.SENT]

    avg_latency = (
        sum(a.latency_minutes for a in human_responses if a.latency_minutes is not None)
        / len(human_responses)
        if human_responses
        else None
    )

    ignored_rate = (len(ignored) / total_tests * 100) if total_tests else 0
    human_rate = (len(human_responses) / total_tests * 100) if total_tests else 0

    # --- Classement par agence (résumé) ---
    rankings = []
    for agency in agencies:
        agency_audits = [a for a in audits if a.agency_id == agency.id]
        agency_human = [a for a in agency_audits if a.test_status == TestStatus.RESPONDED_HUMAN]
        agency_ignored = [a for a in agency_audits if a.test_status == TestStatus.IGNORED]

        agency_avg_latency = (
            sum(a.latency_minutes for a in agency_human if a.latency_minutes is not None)
            / len(agency_human)
            if agency_human
            else None
        )
        agency_ignored_rate = (len(agency_ignored) / len(agency_audits) * 100) if agency_audits else 0

        rankings.append(
            {
                "name": agency.name,
                "avg_latency": round(agency_avg_latency, 1) if agency_avg_latency is not None else None,
                "ignored_rate": round(agency_ignored_rate, 1),
            }
        )

    rankings.sort(key=lambda r: (r["avg_latency"] is None, r["avg_latency"] or 0))

    # --- Détail par test (pour le rapport) ---
    audits_detail = []
    for audit in audits:
        agency = agency_by_id.get(audit.agency_id)
        audits_detail.append(
            {
                "agency_name": agency.name if agency else "—",
                "property_title": audit.property_title or "—",
                "property_price": audit.property_price or None,
                "test_index": audit.test_index,
                "sent_at": audit.sent_at.isoformat() if audit.sent_at else None,
                "received_at": audit.received_at.isoformat() if audit.received_at else None,
                "latency_minutes": audit.latency_minutes,
                "status": audit.test_status.value if audit.test_status else None,
                "quality": _quality_label(audit),
            }
        )

    # --- Rapport par agence (pour export copiable / génération IA) ---
    agency_reports = []
    for agency in agencies:
        agency_audits = [a for a in audits if a.agency_id == agency.id]
        agency_human = [a for a in agency_audits if a.test_status == TestStatus.RESPONDED_HUMAN]
        agency_ignored = [a for a in agency_audits if a.test_status == TestStatus.IGNORED]

        agency_avg_latency = (
            sum(a.latency_minutes for a in agency_human if a.latency_minutes is not None)
            / len(agency_human)
            if agency_human
            else None
        )
        agency_human_rate = (len(agency_human) / len(agency_audits) * 100) if agency_audits else 0
        agency_ignored_rate = (len(agency_ignored) / len(agency_audits) * 100) if agency_audits else 0

        tests = []
        for a in sorted(agency_audits, key=lambda x: x.test_index):
            tests.append(
                {
                    "test_index": a.test_index,
                    "property_title": a.property_title or "—",
                    "property_price": a.property_price or None,
                    "sent_at": a.sent_at.isoformat() if a.sent_at else None,
                    "received_at": a.received_at.isoformat() if a.received_at else None,
                    "latency_minutes": a.latency_minutes,
                    "status": a.test_status.value if a.test_status else None,
                    "quality": _quality_label(a),
                }
            )

        days_since_contact = None
        if agency.last_contact_at:
            delta = datetime.now(timezone.utc) - agency.last_contact_at
            days_since_contact = delta.days

        relance_due = (
            agency.prospecting_status in (ProspectingStatus.EN_COURS, ProspectingStatus.APPELE, ProspectingStatus.RELANCE_1)
            and days_since_contact is not None
            and days_since_contact >= RELANCE_DUE_AFTER_DAYS
            and agency.relance_count < 2
        )

        agency_reports.append(
            {
                "id": agency.id,
                "name": agency.name,
                "public_email": agency.public_email,
                "catalog_url": agency.catalog_url,
                "audit_status": agency.audit_status.value if agency.audit_status else None,
                "tests_sent": len(agency_audits),
                "human_response_rate": round(agency_human_rate, 1),
                "ignored_rate": round(agency_ignored_rate, 1),
                "avg_latency": round(agency_avg_latency, 1) if agency_avg_latency is not None else None,
                "tests": tests,
                "prospecting_status": agency.prospecting_status.value if agency.prospecting_status else "NON_CONTACTEE",
                "relance_count": agency.relance_count or 0,
                "days_since_contact": days_since_contact,
                "relance_due": relance_due,
            }
        )

    pipeline_summary = {}
    for status in ProspectingStatus:
        pipeline_summary[status.value] = sum(
            1 for ag in agency_reports if ag["prospecting_status"] == status.value
        )

    return {
        "agencies_audited": len(agencies),
        "total_tests_sent": total_tests,
        "avg_response_time_minutes": round(avg_latency, 1) if avg_latency is not None else None,
        "ignored_rate_percent": round(ignored_rate, 1),
        "human_response_rate_percent": round(human_rate, 1),
        "agency_rankings": rankings,
        "response_breakdown": {
            "human": len(human_responses),
            "auto": len(auto_responses),
            "ignored": len(ignored),
            "pending": len(pending),
        },
        "audits_detail": audits_detail,
        "agency_reports": agency_reports,
        "pipeline_summary": pipeline_summary,
    }


def _to_csv(metrics: dict) -> str:
    """Export détaillé, un test par ligne : utilisable directement comme
    rapport (import Excel/Notion, preuve chiffrée en argumentaire commercial)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "agency_name",
            "property_title",
            "property_price",
            "test_index",
            "sent_at",
            "received_at",
            "latency_minutes",
            "status",
            "quality",
        ]
    )
    for row in metrics["audits_detail"]:
        writer.writerow(
            [
                row["agency_name"],
                row["property_title"],
                row["property_price"],
                row["test_index"],
                row["sent_at"],
                row["received_at"],
                row["latency_minutes"],
                row["status"],
                row["quality"],
            ]
        )
    return buffer.getvalue()


# Note : le routage HTTP est géré par api/index.py (point d'entrée unique
# FastAPI exigé par le runtime Python Vercel). Ce module n'expose plus que
# des fonctions utilitaires (_build_metrics, _to_csv), importées depuis là.
