"""
Point d'entrée unique Vercel Python (exigé par le runtime Python actuel :
un seul fichier app.py/index.py/main.py exposant une app FastAPI/Flask/Django).

Toutes les routes (dashboard + 3 cron jobs) sont regroupées ici et
délèguent leur logique aux modules dédiés (api/dashboard.py et
api/cron/*.py), qui exposent chacun une fonction run()/_build_metrics().
"""
import json
import logging

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from api.dashboard import _build_metrics, _to_csv
from api.cron import check_inbox, find_leads, send_tests
from api.cron.send_tests import NEW_AGENCY_DAILY_CAP
from api.agencies_import import import_agencies
from lib.db import get_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

TOTAL_TESTS_PER_AGENCY = 3  # protocole : jours 1 / 4 / 9

QUALITY_COLORS = {
    "Excellent": "#22c55e",
    "Bon": "#84cc16",
    "Moyen": "#eab308",
    "Lent": "#f97316",
    "Auto-réponse": "#64748b",
    "Ignoré": "#ef4444",
    "En attente": "#3b82f6",
    "Répondu": "#84cc16",
    "—": "#64748b",
}


def _fmt_dt(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    # Coupe l'ISO string proprement pour un affichage compact JJ/MM HH:MM
    try:
        date_part, time_part = iso_str.split("T")
        y, m, d = date_part.split("-")
        hm = time_part[:5]
        return f"{d}/{m} {hm}"
    except Exception:  # noqa: BLE001
        return iso_str


def _dashboard_html(metrics: dict) -> str:
    rankings = metrics["agency_rankings"]
    detail = metrics["audits_detail"]
    breakdown = metrics["response_breakdown"]
    agency_reports = metrics["agency_reports"]

    rows_html = "".join(
        f"""
        <tr>
          <td>{r['name']}</td>
          <td>{r['avg_latency'] if r['avg_latency'] is not None else '—'}</td>
          <td>{r['ignored_rate']}%</td>
        </tr>
        """
        for r in rankings
    ) or "<tr><td colspan='3' class='empty'>Aucune donnée pour le moment.</td></tr>"

    def _quality_badge(q: str) -> str:
        color = QUALITY_COLORS.get(q, "#64748b")
        return f"<span class='badge' style='background:{color}22;color:{color};border:1px solid {color}55'>{q}</span>"

    detail_rows_html = "".join(
        f"""
        <tr>
          <td>{a['agency_name']}</td>
          <td>{a['property_title']}<span class="muted"> · test {a['test_index']}</span></td>
          <td>{a['property_price'] or '—'}</td>
          <td>{a['latency_minutes'] if a['latency_minutes'] is not None else '—'}</td>
          <td>{_quality_badge(a['quality'])}</td>
        </tr>
        """
        for a in detail
    ) or "<tr><td colspan='5' class='empty'>Aucun test envoyé pour le moment.</td></tr>"

    timeline_html = "".join(
        f"""
        <div class="timeline-item">
          <div class="dot" style="background:{QUALITY_COLORS.get(a['quality'], '#64748b')}"></div>
          <div class="timeline-content">
            <div class="timeline-head">
              <strong>{a['agency_name']}</strong>
              {_quality_badge(a['quality'])}
            </div>
            <div class="muted">{a['property_title']} — envoyé le {_fmt_dt(a['sent_at'])}
              {f" · reçu le {_fmt_dt(a['received_at'])}" if a['received_at'] else ""}
            </div>
          </div>
        </div>
        """
        for a in detail[:15]
    ) or "<div class='empty'>Aucune activité pour le moment.</div>"

    chart_labels = json.dumps([r["name"] for r in rankings], ensure_ascii=False)
    chart_data = json.dumps([r["avg_latency"] or 0 for r in rankings])

    pie_labels = json.dumps(["Réponse humaine", "Auto-réponse", "Ignoré", "En attente"], ensure_ascii=False)
    pie_data = json.dumps([breakdown["human"], breakdown["auto"], breakdown["ignored"], breakdown["pending"]])

    agency_reports_json = json.dumps(agency_reports, ensure_ascii=False)

    agency_cards_html = "".join(
        f"""
        <div class="agency-card">
          <div class="agency-card-head">
            <div>
              <strong>{ag['name']}</strong>
              <div class="muted">{ag['public_email']}</div>
            </div>
            <button class="copy-btn" onclick="copyAgency({i}, this)">Copier</button>
          </div>
          <div class="agency-stats">
            <span>{ag['tests_sent']}/{3} tests</span>
            <span>·</span>
            <span>{ag['human_response_rate']}% humain</span>
            <span>·</span>
            <span>{ag['avg_latency'] if ag['avg_latency'] is not None else '—'} min moy.</span>
            <span>·</span>
            <span>{ag['ignored_rate']}% ignoré</span>
          </div>
        </div>
        """
        for i, ag in enumerate(agency_reports)
    ) or "<div class='empty'>Aucune agence auditée pour le moment.</div>"

    avg_response = (
        f"{metrics['avg_response_time_minutes']} min"
        if metrics["avg_response_time_minutes"] is not None
        else "—"
    )

    return f"""
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>INFRA S.C.I™ — Audit de Réactivité</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0b0d12;
    --card: #12151c;
    --border: #232733;
    --text: #e8eaed;
    --muted: #8b909c;
    --accent: #f97316;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 32px;
  }}
  h1 {{ font-size: 20px; letter-spacing: 0.5px; margin: 0 0 4px 0; }}
  .subtitle {{ color: var(--muted); font-size: 13px; margin-bottom: 28px; }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
  }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .card .value {{ font-size: 26px; font-weight: 600; color: var(--accent); }}
  .card .label {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1.3fr 1fr; gap: 20px; margin-bottom: 24px; }}
  @media (max-width: 860px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 24px; }}
  .panel h2 {{ font-size: 14px; color: var(--muted); margin: 0 0 16px 0; text-transform: uppercase; letter-spacing: 0.5px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 500; text-transform: uppercase; font-size: 11px; }}
  .empty {{ color: var(--muted); text-align: center; padding: 24px; }}
  .muted {{ color: var(--muted); font-size: 12px; }}
  a.export {{ color: var(--accent); font-size: 12px; text-decoration: none; }}
  canvas {{ max-height: 280px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
  .timeline-item {{ display: flex; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }}
  .timeline-item:last-child {{ border-bottom: none; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }}
  .timeline-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 2px; font-size: 13px; }}
  .agency-card {{ border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 10px; }}
  .agency-card-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }}
  .agency-stats {{ margin-top: 8px; font-size: 12px; color: var(--muted); display: flex; gap: 6px; flex-wrap: wrap; }}
  .copy-btn {{
    background: transparent; border: 1px solid var(--accent); color: var(--accent);
    border-radius: 6px; padding: 5px 12px; font-size: 12px; cursor: pointer; white-space: nowrap;
  }}
  .copy-btn:hover {{ background: var(--accent); color: #0b0d12; }}
  .copy-btn.copied {{ background: #22c55e; border-color: #22c55e; color: #0b0d12; }}
  .panel-head-row {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
  .panel-head-row h2 {{ margin: 0; }}
  textarea#import-input {{
    width: 100%; min-height: 140px; background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; padding: 10px;
    font-family: ui-monospace, monospace; font-size: 12px; resize: vertical;
  }}
</style>
</head>
<body>
  <h1>Audit de Réactivité — INFRA S.C.I™</h1>
  <div class="subtitle">Mystery-client audit automatisé — agences immobilières</div>

  <div class="cards">
    <div class="card"><div class="value">{metrics['agencies_audited']}</div><div class="label">Agences auditées</div></div>
    <div class="card"><div class="value">{metrics['total_tests_sent']}</div><div class="label">Tests envoyés</div></div>
    <div class="card"><div class="value">{avg_response}</div><div class="label">Latence moyenne</div></div>
    <div class="card"><div class="value">{metrics['ignored_rate_percent']}%</div><div class="label">Taux d'ignoré</div></div>
    <div class="card"><div class="value">{metrics['human_response_rate_percent']}%</div><div class="label">Réponse humaine</div></div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <h2>Latence de réponse par agence (minutes)</h2>
      <canvas id="latencyChart"></canvas>
    </div>
    <div class="panel">
      <h2>Répartition des réponses</h2>
      <canvas id="breakdownChart"></canvas>
    </div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <h2>Détail des tests (rapport)</h2>
      <table>
        <thead><tr><th>Agence</th><th>Bien testé</th><th>Prix</th><th>Latence (min)</th><th>Qualité</th></tr></thead>
        <tbody>{detail_rows_html}</tbody>
      </table>
      <div style="margin-top:14px;">
        <a class="export" href="/api/dashboard?format=csv">↓ Exporter le rapport complet en CSV</a>
      </div>
    </div>
    <div class="panel">
      <h2>Activité récente</h2>
      {timeline_html}
    </div>
  </div>

  <div class="panel">
    <h2>Importer des agences</h2>
    <div class="muted" style="margin-bottom:12px;">
      Colle un tableau JSON d'agences (nom, email, site, jusqu'à 3 biens). Elles seront testées automatiquement
      selon le planning habituel (max {NEW_AGENCY_DAILY_CAP} nouveaux contacts/jour, pour préserver la réputation des adresses d'envoi).
    </div>
    <textarea id="import-input" placeholder='[
  {{
    "name": "BARNES Paris 8e",
    "public_email": "contact@barnes-paris8.com",
    "catalog_url": "https://...",
    "properties": [
      {{"property_title": "Duplex 180m² vue Tour Eiffel", "property_url": "https://...", "property_price": "3 200 000 €"}}
    ]
  }}
]'></textarea>
    <div style="margin-top:10px; display:flex; align-items:center; gap:12px;">
      <button class="copy-btn" onclick="importAgencies(this)">Importer</button>
      <span id="import-status" class="muted"></span>
    </div>
  </div>

  <div class="panel">
    <h2>Classement des agences</h2>
    <table>
      <thead><tr><th>Agence</th><th>Latence moy. (min)</th><th>Taux ignoré</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div class="panel">
    <div class="panel-head-row">
      <h2>Rapport par agence — pour argumentaire B2B</h2>
      <button class="copy-btn" onclick="copyAll(this)">Copier tout le rapport</button>
    </div>
    <div class="muted" style="margin-bottom:14px;">
      Copie les infos d'une agence (ou de toutes) au format texte, prêtes à coller dans une IA pour rédiger ton document B2B.
    </div>
    {agency_cards_html}
  </div>

  <script>
    const agencyReports = {agency_reports_json};

    function fmtDate(iso) {{
      if (!iso) return null;
      const [datePart, timePart] = iso.split('T');
      const [y, m, d] = datePart.split('-');
      return `${{d}}/${{m}}/${{y}} ${{timePart.slice(0,5)}}`;
    }}

    function formatAgencyReport(ag) {{
      const TOTAL_TESTS = 3;
      let lines = [];
      lines.push(`=== ${{ag.name}} ===`);
      if (ag.public_email) lines.push(`Email : ${{ag.public_email}}`);
      if (ag.catalog_url) lines.push(`Site : ${{ag.catalog_url}}`);
      lines.push(`Protocole : ${{ag.tests_sent}}/${{TOTAL_TESTS}} tests envoyés${{ag.tests_sent < TOTAL_TESTS ? ' (audit en cours, ' + (TOTAL_TESTS - ag.tests_sent) + ' restant(s))' : ' (audit complet)'}}`);
      lines.push(`Taux de réponse humaine : ${{ag.human_response_rate}}%`);
      lines.push(`Latence moyenne : ${{ag.avg_latency !== null ? ag.avg_latency + ' min' : 'N/A'}}`);
      lines.push(`Taux d'ignoré : ${{ag.ignored_rate}}%`);
      lines.push('');
      lines.push('Détail des tests :');
      ag.tests.forEach(t => {{
        const sent = fmtDate(t.sent_at) || 'N/A';
        const received = fmtDate(t.received_at);
        const price = t.property_price ? ` (${{t.property_price}})` : '';
        let line = `- Test ${{t.test_index}}/${{TOTAL_TESTS}} : ${{t.property_title}}${{price}} — envoyé le ${{sent}}`;
        if (received) line += `, reçu le ${{received}} (${{t.latency_minutes}} min)`;
        line += ` — Qualité : ${{t.quality}}`;
        lines.push(line);
      }});

      // Constat automatique : utile comme accroche dans l'argumentaire B2B
      if (ag.ignored_rate > 0 && ag.human_response_rate > 0) {{
        lines.push('');
        lines.push(`Constat : réactivité inconsistante — l'agence répond parfois, mais ignore ${{ag.ignored_rate}}% des demandes testées.`);
      }} else if (ag.ignored_rate === 100) {{
        lines.push('');
        lines.push(`Constat : aucune réponse obtenue sur l'ensemble des tests envoyés.`);
      }} else if (ag.human_response_rate === 100 && ag.avg_latency !== null && ag.avg_latency <= 60) {{
        lines.push('');
        lines.push(`Constat : réactivité excellente sur l'ensemble des tests (réponse humaine sous ${{Math.round(ag.avg_latency)}} min en moyenne).`);
      }}

      return lines.join('\\n');
    }}

    function flashCopied(btn) {{
      const original = btn.textContent;
      btn.textContent = 'Copié !';
      btn.classList.add('copied');
      setTimeout(() => {{ btn.textContent = original; btn.classList.remove('copied'); }}, 1500);
    }}

    function copyAgency(index, btn) {{
      const text = formatAgencyReport(agencyReports[index]);
      navigator.clipboard.writeText(text).then(() => flashCopied(btn));
    }}

    function copyAll(btn) {{
      const text = agencyReports.map(formatAgencyReport).join('\\n\\n');
      navigator.clipboard.writeText(text).then(() => flashCopied(btn));
    }}

    async function importAgencies(btn) {{
      const statusEl = document.getElementById('import-status');
      const raw = document.getElementById('import-input').value.trim();
      if (!raw) {{
        statusEl.textContent = 'Colle un JSON avant d\\'importer.';
        return;
      }}

      let parsed;
      try {{
        parsed = JSON.parse(raw);
      }} catch (e) {{
        statusEl.textContent = 'JSON invalide : ' + e.message;
        return;
      }}

      btn.disabled = true;
      statusEl.textContent = 'Import en cours...';

      try {{
        const res = await fetch('/api/agencies/import', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(parsed),
        }});
        const data = await res.json();
        if (!res.ok) {{
          statusEl.textContent = 'Erreur : ' + (data.error || res.statusText);
        }} else {{
          statusEl.textContent = `${{data.agencies_created}} agence(s) créée(s), ${{data.duplicates_skipped}} doublon(s) ignoré(s).`;
          if (data.errors && data.errors.length) {{
            statusEl.textContent += ' Erreurs : ' + data.errors.join(' | ');
          }}
          if (data.agencies_created > 0) {{
            setTimeout(() => location.reload(), 1800);
          }}
        }}
      }} catch (e) {{
        statusEl.textContent = 'Erreur réseau : ' + e.message;
      }} finally {{
        btn.disabled = false;
      }}
    }}

    new Chart(document.getElementById('latencyChart'), {{
      type: 'bar',
      data: {{
        labels: {chart_labels},
        datasets: [{{ label: 'Latence moyenne (min)', data: {chart_data}, backgroundColor: '#f97316', borderRadius: 4 }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#232733' }}, ticks: {{ color: '#8b909c' }} }},
          x: {{ grid: {{ display: false }}, ticks: {{ color: '#8b909c' }} }}
        }}
      }}
    }});

    new Chart(document.getElementById('breakdownChart'), {{
      type: 'doughnut',
      data: {{
        labels: {pie_labels},
        datasets: [{{
          data: {pie_data},
          backgroundColor: ['#22c55e', '#64748b', '#ef4444', '#3b82f6'],
          borderColor: '#12151c',
          borderWidth: 2,
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#e8eaed', font: {{ size: 11 }} }} }} }}
      }}
    }});
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard_view():
    with get_session() as session:
        metrics = _build_metrics(session)
    return HTMLResponse(content=_dashboard_html(metrics))


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


@app.post("/api/agencies/import")
async def agencies_import(request: Request):
    body = await request.json()
    if not isinstance(body, list):
        return JSONResponse(
            status_code=400,
            content={"error": "Le corps doit être un tableau JSON d'agences."},
        )
    result = import_agencies(body)
    return JSONResponse(content=result)


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
