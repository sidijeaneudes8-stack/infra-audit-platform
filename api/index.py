"""
Point d'entrée unique Vercel Python (exigé par le runtime Python actuel :
un seul fichier app.py/index.py/main.py exposant une app FastAPI/Flask/Django).

Toutes les routes (dashboard + 3 cron jobs) sont regroupées ici et
délèguent leur logique aux modules dédiés (api/dashboard.py et
api/cron/*.py), qui exposent chacun une fonction run()/_build_metrics().
"""
import json
import logging

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from api.dashboard import _build_metrics, _to_csv
from api.cron import check_inbox, find_leads, send_tests
from lib.db import get_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


@app.get("/api/dashboard")
def dashboard(format: str | None = Query(default=None)):
    with get_session() as session:
        metrics = _build_metrics(session)

    if format == "csv":
        return PlainTextResponse(
            content=_to_csv(metrics),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_dashboard.csv"},
        )

    return JSONResponse(content=json.loads(json.dumps(metrics, ensure_ascii=False)))


@app.get("/api/cron/find_leads")
@app.post("/api/cron/find_leads")
def cron_find_leads():
    result = find_leads.run()
    return JSONResponse(content=result)


@app.get("/api/cron/send_tests")
@app.post("/api/cron/send_tests")
def cron_send_tests():
    result = send_tests.run()
    return JSONResponse(content=result)


@app.get("/api/cron/check_inbox")
@app.post("/api/cron/check_inbox")
def cron_check_inbox():
    result = check_inbox.run()
    return JSONResponse(content=result)
