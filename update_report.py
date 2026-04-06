"""
update_report.py
Fetches the 4 OnBoarding views from PostHog Data Warehouse,
injects the data into the HTML template, and sends a Slack notification.
"""

import os
import re
import json
import datetime
import urllib.request
import urllib.error

POSTHOG_API_KEY  = os.environ["POSTHOG_API_KEY"]
POSTHOG_PROJECT  = os.environ.get("POSTHOG_PROJECT", "260911")
SLACK_WEBHOOK    = os.environ["SLACK_WEBHOOK_URL"]
POSTHOG_BASE     = f"https://us.posthog.com/api/projects/{POSTHOG_PROJECT}"
TEMPLATE_FILE    = "template.html"
OUTPUT_FILE      = "_deploy/index.html"

VIEWS = {
    "P_RAW":    "OnBoarding_Blocks",
    "U_DATA":   "OnBoarding_TableroControl",
    "M1_DATA":  "OnBoarding_ForecastM1",
    "F4_DATA":  "OnBoarding_ControlFacturacion",
}


def posthog_query(sql):
    url  = f"{POSTHOG_BASE}/query/"
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode()
    req  = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {POSTHOG_API_KEY}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PostHog API error {e.code}: {body_text[:500]}") from e
    columns = [c if isinstance(c, str) else c.get("name", c) for c in data["columns"]]
    return [dict(zip(columns, row)) for row in data["results"]]


def fetch_view(var_name, view_name):
    print(f"  Fetching {view_name} -> {var_name} ...", end=" ", flush=True)
    rows = posthog_query(f"SELECT * FROM {view_name}")
    print(f"{len(rows)} rows")
    return rows


def json_serial(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def to_js_json(data):
    return json.dumps(data, default=json_serial, ensure_ascii=False, indent=None)


def update_html(datasets):
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Inject data arrays
    for var_name, rows in datasets.items():
        new_js  = to_js_json(rows)
        pattern = rf"(const {re.escape(var_name)}\s*=\s*)\[.*?\](?=\s*;)"
        # Lambda prevents re.subn from interpreting \n in new_js as literal newlines
        js_snap = new_js
        html, n = re.subn(pattern, lambda m, js=js_snap: m.group(1) + js, html, count=1, flags=re.DOTALL)
        if n == 0:
            raise ValueError(f"Could not find 'const {var_name} = [...]' in template.")
        print(f"  OK {var_name} injected ({len(rows)} rows)")

    # 2. Update TODAY / TODAY_F  ← THE KEY FIX
    #    The template has hardcoded dates like new Date('2026-03-27').
    #    Without updating them, daysFrom() returns wrong/negative values
    #    and the entire pipeline section renders empty.
    today_iso   = datetime.date.today().isoformat()
    date_re     = re.compile(r"new Date\('\d{4}-\d{2}-\d{2}'\)")
    replacement = "new Date('" + today_iso + "')"
    html, n_d   = date_re.subn(replacement, html)
    print(f"  OK TODAY updated -> {today_iso} ({n_d} occurrences)")

    # 3. Update footer stamps
    today_fmt = datetime.date.today().strftime("%d %b %Y")
    html = re.sub(r"HubSpot \xb7 \w+ \d{4}", f"PostHog \xb7 {today_fmt}", html)
    html = re.sub(r"PostHog \xb7 \d{2} \w{3} \d{4}", f"PostHog \xb7 {today_fmt}", html)

    return html


def send_slack(datasets):
    p_rows  = datasets["P_RAW"]
    u_rows  = datasets["U_DATA"]
    m1_rows = datasets["M1_DATA"]
    f4_rows = datasets["F4_DATA"]

    blocked     = sum(1 for r in p_rows if r.get("descripcion_block"))
    sin_uso     = sum(1 for r in u_rows if (r.get("usos_totales_ultimos_7d") or 0) == 0)
    today_s     = datetime.date.today().isoformat()
    m1_venc     = sum(1 for r in m1_rows
                      if not r.get("fecha_m1_facturado")
                      and r.get("fecha_forecast_m1")
                      and str(r["fecha_forecast_m1"]) < today_s)
    completaron = sum(1 for r in f4_rows if "Completó 4" in (r.get("estado_pago") or ""))

    today_fmt  = datetime.date.today().strftime("%d %b %Y")
    report_url = "https://fgil-payana.github.io/onboarding-report/"

    payload = {"blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": f"Reporte Onboarding - {today_fmt}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Pipeline bloqueado*\n{len(p_rows)} negocios, {blocked} con bloqueo"},
            {"type": "mrkdwn", "text": f"*Uso Entrenamiento*\n{len(u_rows)} ws, {sin_uso} sin uso 7d"},
            {"type": "mrkdwn", "text": f"*Proyeccion M1*\n{len(m1_rows)} pipeline, {m1_venc} vencidos"},
            {"type": "mrkdwn", "text": f"*Facturas*\n{len(f4_rows)} clientes, {completaron} con 4 facturas"},
        ]},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "Ver reporte completo"},
            "url": report_url,
            "style": "primary"
        }]}
    ]}

    body = json.dumps(payload).encode()
    req  = urllib.request.Request(SLACK_WEBHOOK, data=body,
                                   headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"  Slack -> {resp.status}")


def main():
    print("=== Payana Onboarding Report ===")
    print(f"Date: {datetime.date.today().isoformat()}\n")

    print("1. Fetching views from PostHog...")
    datasets = {var: fetch_view(var, view) for var, view in VIEWS.items()}

    print("\n2. Injecting into HTML template...")
    updated_html = update_html(datasets)

    os.makedirs("_deploy", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(updated_html)
    print(f"\n3. Written -> {OUTPUT_FILE}")

    print("\n4. Sending Slack notification...")
    send_slack(datasets)

    print("\nDone.")


if __name__ == "__main__":
    main()
