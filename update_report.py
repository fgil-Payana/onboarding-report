"""
update_report.py
Fetches the 4 OnBoarding views from PostHog Data Warehouse,
injects the data into the HTML template, and saves stats for Slack.
"""

import os
import re
import json
import datetime
import urllib.request
import urllib.error

POSTHOG_API_KEY  = os.environ.get("POSTHOG_API_KEY", "")
POSTHOG_PROJECT  = os.environ.get("POSTHOG_PROJECT", "260911")
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
        return None 

def json_serial(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def to_js_json(data):
    """One JSON object per line — avoids Chrome's 32KB single-line limit."""
    rows = [json.dumps(row, default=json_serial, ensure_ascii=False) for row in data]
    return "[\n" + ",\n".join(rows) + "\n]"

def extract_js_array(html, var_name):
    idx = html.find(f"const {var_name} = [")
    if idx == -1: return None, None, None
    start = html.find("[", idx)
    depth, in_string, escape, pos = 0, False, False, start
    while pos < len(html):
        c = html[pos]
        if escape: escape = False
        elif c == "\\" and in_string: escape = True
        elif c == '"' and not escape: in_string = not in_string
        elif not in_string:
            if c == "[": depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0: return start, pos + 1, html[start : pos + 1]
        pos += 1
    return None, None, None

def reformat_existing_arrays(html):
    for var_name in ["P_RAW", "U_DATA", "M1_DATA", "F4_DATA"]:
        start, end, js_str = extract_js_array(html, var_name)
        if start is None: continue
        if "\n" not in js_str and len(js_str) > 500:
            cleaned = re.sub(r"//[^\n]*", "", js_str)
            try:
                data = json.loads(cleaned)
                new_js = to_js_json(data)
                html = html[:start] + new_js + html[end:]
            except: pass
    return html

def update_html(datasets):
    with open(TEMPLATE_FILE, "r", encoding="utf-8", newline="") as f:
        html = f.read()
    html = html.replace("\r\n", "\n").replace("\r", "\n")

    for var_name, rows in datasets.items():
        if rows is None: continue
        new_js  = to_js_json(rows)
        pattern = rf"(const {re.escape(var_name)}\s*=\s*)\[.*?\](?=\s*;)"
        html, n = re.subn(pattern, lambda m: m.group(1) + new_js, html, count=1, flags=re.DOTALL)
        if n == 0: print(f"  WARNING: Could not find 'const {var_name}'")

    html = reformat_existing_arrays(html)
    today_iso = datetime.date.today().isoformat()
    html = re.sub(r"new Date\('\d{4}-\d{2}-\d{2}'\)", f"new Date('{today_iso}')", html)
    
    today_fmt = datetime.date.today().strftime("%d %b %Y")
    html = re.sub(r"PostHog \xb7 \d{2} \w{3} \d{4}", f"PostHog \xb7 {today_fmt}", html)
    return html

def main():
    if not POSTHOG_API_KEY:
        raise EnvironmentError("POSTHOG_API_KEY env var is required")
    
    print("=== Payana Report Generator ===")
    
    print("1. Fetching data...")
    datasets = {var: fetch_view(var, view) for var, view in VIEWS.items()}

    print("\n2. Updating HTML...")
    updated_html = update_html(datasets)

    os.makedirs("_deploy", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(updated_html)

    print("\n3. Saving Stats for Slack...")
    today_s = datetime.date.today().isoformat()
    p_rows  = datasets.get("P_RAW") or []
    u_rows  = datasets.get("U_DATA") or []
    m1_rows = datasets.get("M1_DATA") or []
    f4_rows = datasets.get("F4_DATA") or []
    
    stats = {
        "p_total":    len(p_rows),
        "p_blocked":  sum(1 for r in p_rows if r.get("descripcion_block")),
        "u_total":    len(u_rows),
        "u_sin_uso":  sum(1 for r in u_rows if (r.get("usos_totales_ultimos_7d") or 0) == 0),
        "m1_total":   len(m1_rows),
        "m1_venc":    sum(1 for r in m1_rows if not r.get("fecha_m1_facturado") and r.get("fecha_forecast_m1") and str(r["fecha_forecast_m1"]) < today_s),
        "f4_total":   len(f4_rows),
        "f4_completo": sum(1 for r in f4_rows if "Completó 4" in (r.get("estado_pago") or "")),
    }
    
    with open("/tmp/report_stats.json", "w") as f:
        json.dump(stats, f)

    print("Done. Workflow will handle Slack.")

if __name__ == "__main__":
    main()
