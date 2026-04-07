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

POSTHOG_API_KEY  = os.environ.get("POSTHOG_API_KEY", "")
POSTHOG_PROJECT  = os.environ.get("POSTHOG_PROJECT", "260911")
SLACK_WEBHOOK    = os.environ.get("SLACK_WEBHOOK_URL", "")
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
    try:
        rows = posthog_query(f"SELECT * FROM {view_name}")
        print(f"{len(rows)} rows")
        return rows
    except Exception as e:
        print(f"ERROR: {e}")
        print(f"  WARNING: {var_name} fallo — se conservaran los datos existentes en el template.")
        return None  # None = mantener los datos existentes en el HTML


def json_serial(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def to_js_json(data):
    """One JSON object per line — avoids Chrome's 32KB single-line limit."""
    rows = [json.dumps(row, default=json_serial, ensure_ascii=False) for row in data]
    return "[\n" + ",\n".join(rows) + "\n]"


def extract_js_array(html, var_name):
    """
    Extract a JS array by bracket matching.
    Handles arrays with JS comments inside (e.g. // SET UP).
    Returns (start_pos, end_pos, array_string) or (None, None, None).
    """
    idx = html.find(f"const {var_name} = [")
    if idx == -1:
        return None, None, None
    start = html.find("[", idx)
    depth, in_string, escape, pos = 0, False, False, start
    while pos < len(html):
        c = html[pos]
        if escape:
            escape = False
        elif c == "\\" and in_string:
            escape = True
        elif c == '"' and not escape:
            in_string = not in_string
        elif not in_string:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return start, pos + 1, html[start : pos + 1]
        pos += 1
    return None, None, None


def reformat_existing_arrays(html):
    """
    Post-processing safety net: find any const VAR = [...] blocks that
    ended up on a single long line and reformat them to one-row-per-line.
    This handles the case where the template itself has long inline arrays
    that weren't replaced (e.g. HS_DATE or leftover data from a prior run).
    """
    for var_name in ["P_RAW", "U_DATA", "M1_DATA", "F4_DATA"]:
        start, end, js_str = extract_js_array(html, var_name)
        if start is None:
            continue
        # Only reformat if it's a single long line (>500 chars with no newlines)
        if "\n" not in js_str and len(js_str) > 500:
            # Strip JS comments, parse, reformat
            cleaned = re.sub(r"//[^\n]*", "", js_str)
            try:
                data = json.loads(cleaned)
                new_js = to_js_json(data)
                html = html[:start] + new_js + html[end:]
                print(f"  Reformatted existing {var_name} ({len(js_str)} → multiline)")
            except Exception as e:
                print(f"  WARNING: could not reformat {var_name}: {e}")
    return html


def update_html(datasets):
    with open(TEMPLATE_FILE, "r", encoding="utf-8", newline="") as f:
        html = f.read()
    # Normalize line endings — Git on Windows/some runners may add CRLF
    html = html.replace("\r\n", "\n").replace("\r", "\n")

    # 1. Inject fresh data arrays (one row per line)
    for var_name, rows in datasets.items():
        if rows is None:
            print(f"  SKIP {var_name} (view error — keeping existing data)")
            continue
        new_js  = to_js_json(rows)
        pattern = rf"(const {re.escape(var_name)}\s*=\s*)\[.*?\](?=\s*;)"
        js_snap = new_js
        # Lambda avoids re.subn interpreting \n in new_js as literal newlines
        html, n = re.subn(
            pattern,
            lambda m, js=js_snap: m.group(1) + js,
            html,
            count=1,
            flags=re.DOTALL,
        )
        if n == 0:
            raise ValueError(f"Could not find 'const {var_name} = [...]' in template.")
        print(f"  OK {var_name} injected ({len(rows)} rows)")

    # 2. Safety net: reformat any remaining single-line arrays
    #    (e.g. HS_DATE or arrays that were already in the template as data)
    html = reformat_existing_arrays(html)

    # 3. Update TODAY / TODAY_F
    today_iso   = datetime.date.today().isoformat()
    date_re     = re.compile(r"new Date\('\d{4}-\d{2}-\d{2}'\)")
    replacement = "new Date('" + today_iso + "')"
    html, n_d   = date_re.subn(replacement, html)
    print(f"  OK TODAY updated -> {today_iso} ({n_d} occurrences)")

    # 4. Update footer stamps
    today_fmt = datetime.date.today().strftime("%d %b %Y")
    html = re.sub(r"HubSpot \xb7 \w+ \d{4}", f"PostHog \xb7 {today_fmt}", html)
    html = re.sub(r"PostHog \xb7 \d{2} \w{3} \d{4}", f"PostHog \xb7 {today_fmt}", html)

    # 5. Final check: warn if any line is still suspiciously long
    long_lines = [(i + 1, len(l)) for i, l in enumerate(html.split("\n")) if len(l) > 10000]
    if long_lines:
        for ln, llen in long_lines:
            print(f"  WARNING: line {ln} still has {llen} chars — may cause Chrome errors")
    else:
        print("  OK All lines within safe length")

    return html


def send_slack(datasets):
    p_rows  = datasets.get("P_RAW")  or []
    u_rows  = datasets.get("U_DATA") or []
    m1_rows = datasets.get("M1_DATA") or []
    f4_rows = datasets.get("F4_DATA") or []

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
            "style": "primary",
        }]},
    ]}

    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        SLACK_WEBHOOK, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"  Slack -> {resp.status}")


def main():
    if not POSTHOG_API_KEY:
        raise EnvironmentError("POSTHOG_API_KEY env var is required")
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

    # Save stats for Slack notification (read after publish, without re-fetching)
    today_s = datetime.date.today().isoformat()
    p_rows  = datasets.get("P_RAW")  or []
    u_rows  = datasets.get("U_DATA") or []
    m1_rows = datasets.get("M1_DATA") or []
    f4_rows = datasets.get("F4_DATA") or []
    stats = {
        "p_total":    len(p_rows),
        "p_blocked":  sum(1 for r in p_rows if r.get("descripcion_block")),
        "u_total":    len(u_rows),
        "u_sin_uso":  sum(1 for r in u_rows if (r.get("usos_totales_ultimos_7d") or 0) == 0),
        "m1_total":   len(m1_rows),
        "m1_venc":    sum(1 for r in m1_rows
                         if not r.get("fecha_m1_facturado")
                         and r.get("fecha_forecast_m1")
                         and str(r["fecha_forecast_m1"]) < today_s),
        "f4_total":   len(f4_rows),
        "f4_completo": sum(1 for r in f4_rows if "Completó 4" in (r.get("estado_pago") or "")),
    }
    with open("/tmp/report_stats.json", "w") as f:
        json.dump(stats, f)
    print("\n4. Stats saved -> /tmp/report_stats.json")

    print("\nDone.")


if __name__ == "__main__":
    main()
