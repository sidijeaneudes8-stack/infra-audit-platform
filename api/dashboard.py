"""
Vercel Serverless Function : GET /api/dashboard
Retourne les métriques agrégées d'audit. Ajoute ?format=csv pour un export
CSV téléchargeable (usage interne / import Notion, Excel...).
"""
import csv
import io
import json

from lib.db import get_session
from models.schema import Agency, Audit, TestStatus


def _build_metrics(session) -> dict:
    agencies = session.query(Agency).all()
    audits = session.query(Audit).all()

    total_tests = len(audits)
    human_responses = [a for a in audits if a.test_status == TestStatus.RESPONDED_HUMAN]
    ignored = [a for a in audits if a.test_status == TestStatus.IGNORED]

    avg_latency = (
        sum(a.latency_minutes for a in human_responses if a.latency_minutes is not None)
        / len(human_responses)
        if human_responses
        else None
    )

    ignored_rate = (len(ignored) / total_tests * 100) if total_tests else 0
    human_rate = (len(human_responses) / total_tests * 100) if total_tests else 0

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

    return {
        "agencies_audited": len(agencies),
        "total_tests_sent": total_tests,
        "avg_response_time_minutes": round(avg_latency, 1) if avg_latency is not None else None,
        "ignored_rate_percent": round(ignored_rate, 1),
        "human_response_rate_percent": round(human_rate, 1),
        "agency_rankings": rankings,
    }


def _to_csv(metrics: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["agency_name", "avg_latency_minutes", "ignored_rate_percent"])
    for row in metrics["agency_rankings"]:
        writer.writerow([row["name"], row["avg_latency"], row["ignored_rate"]])
    return buffer.getvalue()


def handler(request):
    query_format = None
    try:
        query_format = request.args.get("format")  # selon le runtime Vercel utilisé
    except AttributeError:
        pass

    with get_session() as session:
        metrics = _build_metrics(session)

    if query_format == "csv":
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/csv",
                "Content-Disposition": "attachment; filename=audit_dashboard.csv",
            },
            "body": _to_csv(metrics),
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(metrics, ensure_ascii=False),
    }
