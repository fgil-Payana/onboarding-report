"""
update_report.py
Fetches the 4 OnBoarding views from PostHog Data Warehouse,
injects the data into the HTML template, and sends a Slack notification.

Environment variables required (set as GitHub Secrets):
  POSTHOG_API_KEY   — PostHog project API key
  POSTHOG_PROJECT   — PostHog project ID (260911)
  SLACK_WEBHOOK_URL — Slack incoming webhook URL
"""

import os
import re
import json
import datetime
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
POSTHOG_API_KEY  = os.environ["POSTHOG_API_KEY"]
POSTHOG_PROJECT  = os.environ.get("POSTHOG_PROJECT", "260911")
SLACK_WEBHOOK    = os.environ["SLACK_WEBHOOK_URL"]
POSTHOG_BASE     = f"https://us.posthog.com/api/projects/{POSTHOG_PROJECT}"

TEMPLATE_FILE    = "payana_reporte_unificado.html"
OUTPUT_FILE      = "docs/index.html"      # GitHub Pages serves from docs/ folder

# ── Views to query ────────────────────────────────────────────────────────────
VIEWS = {
    "P_RAW":    "OnBoarding_Blocks",           # Se inyecta como P_RAW (el HTML lo transforma a P_DATA con .map())
    "U_DATA":   "OnBoarding_TableroControl",
    "M1_DATA":  "OnBoarding_ForecastM1",
    "F4_DATA":  "OnBoarding_ControlFacturacion",
}


# ── PostHog query helper ──────────────────────────────────────────────────────
def posthog_query(sql: str) -> list[dict]:
    """Run a HogQL query and return rows as list of dicts."""
    url  = f"{POSTHOG_BASE}/query/"
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {POSTHOG_API_KEY}",
            "Content-Type":  "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"PostHog API error {e.code} for query:\n  {sql}\n"
            f"Response: {body_text[:500]}\n\n"
            f"Tip: Make sure POSTHOG_API_KEY is a Personal API Key (starts with phx_), "
            f"not a Project API key (phc_). Create one at: "
            f"https://us.posthog.com/settings/user-api-keys"
        ) from e

    columns = [c if isinstance(c, str) else c.get("name", c) for c in data["columns"]]
    return [dict(zip(columns, row)) for row in data["results"]]


def fetch_view(var_name: str, view_name: str) -> list[dict]:
    print(f"  Fetching {view_name} → {var_name} ...", end=" ", flush=True)
    rows = posthog_query(f"SELECT * FROM {view_name}")
    print(f"{len(rows)} rows")
    return rows


# ── JSON serializer (handles datetime objects) ────────────────────────────────
def json_serial(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def to_js_json(data: list[dict]) -> str:
    return json.dumps(data, default=json_serial, ensure_ascii=False, indent=None)


# ── Inject data into HTML template ───────────────────────────────────────────
def update_html(datasets: dict[str, list[dict]]) -> str:
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    for var_name, rows in datasets.items():
        new_js = to_js_json(rows)
        # Match:  const VAR_NAME = [...];
        pattern = rf"(const {re.escape(var_name)}\s*=\s*)\[.*?\](?=\s*;)"
        replacement = rf"\g<1>{new_js}"
        updated, count = re.subn(pattern, replacement, html, count=1, flags=re.DOTALL)
        if count == 0:
            raise ValueError(f"Could not find 'const {var_name} = [...]' in template.")
        html = updated
        print(f"  ✓ {var_name} injected ({len(rows)} rows)")

    # Update the date stamp comment near the top of each script block
    today = datetime.date.today().strftime("%d %b %Y")
    html = re.sub(r"HubSpot · \w+ \d{4}", f"PostHog · {today}", html)

    return html


# ── Slack notification ────────────────────────────────────────────────────────
def send_slack(datasets: dict[str, list[dict]]):
    p_rows  = datasets["P_RAW"]
    u_rows  = datasets["U_DATA"]
    m1_rows = datasets["M1_DATA"]
    f4_rows = datasets["F4_DATA"]

    # Quick KPIs for the Slack message
    blocked   = sum(1 for r in p_rows if r.get("descripcion_block"))
    sin_uso   = sum(1 for r in u_rows if (r.get("usos_totales_ultimos_7d") or 0) == 0)
    m1_venc   = sum(1 for r in m1_rows
                    if not r.get("fecha_m1_facturado")
                    and r.get("fecha_forecast_m1")
                    and r["fecha_forecast_m1"] < datetime.date.today().isoformat())
    completaron = sum(1 for r in f4_rows if "Completó 4" in (r.get("estado_pago") or ""))

    today_str = datetime.date.today().strftime("%d %b %Y")
    report_url = "https://fgil-payana.github.io/onboarding-report/"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📊 Reporte Onboarding — " + today_str}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*🔧 Pipeline bloqueado*\n{len(p_rows)} negocios activos · {blocked} con bloqueo"},
                    {"type": "mrkdwn", "text": f"*📊 Uso en entrenamiento*\n{len(u_rows)} workspaces · {sin_uso} sin uso 7d"},
                    {"type": "mrkdwn", "text": f"*📅 Proyección M1*\n{len(m1_rows)} en pipeline · {m1_venc} con forecast vencido"},
                    {"type": "mrkdwn", "text": f"*🧾 Facturas*\n{len(f4_rows)} clientes · {completaron} completaron 4 facturas"},
                ]
            },
            {
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Ver reporte completo →"},
                    "url": report_url,
                    "style": "primary"
                }]
            }
        ]
    }

    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        SLACK_WEBHOOK,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"  Slack → {resp.status}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=== Payana Onboarding Report — Auto-update ===")
    print(f"Date: {datetime.date.today().isoformat()}\n")

    # 1. Fetch all 4 views
    print("1. Fetching views from PostHog...")
    datasets = {var: fetch_view(var, view) for var, view in VIEWS.items()}

    # 2. Inject into HTML
    print("\n2. Injecting data into HTML template...")
    updated_html = update_html(datasets)

    # 3. Write output
    import os; os.makedirs("docs", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(updated_html)
    print(f"\n3. Written → {OUTPUT_FILE}")

    # 4. Slack notification
    print("\n4. Sending Slack notification...")
    send_slack(datasets)

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
