# Payana Onboarding Report — Setup de automatización
## Tiempo estimado: 20 minutos

---

## Estructura del repositorio

```
payana-onboarding-report/          ← nombre del repo (puedes cambiarlo)
├── .github/
│   └── workflows/
│       └── weekly_report.yml      ← el cron de GitHub Actions
├── payana_reporte_unificado.html  ← la plantilla (nunca se sobreescribe)
├── update_report.py               ← el script de actualización
└── index.html                     ← generado automáticamente (no subir)
```

---

## Paso 1 — Crear el repositorio en GitHub

1. Ve a https://github.com/new
2. Nombre: `onboarding-report`
3. Visibilidad: **Private** ✅ (el reporte publicado sí será público via Pages)
4. Crear el repo

---

## Paso 2 — Subir los archivos

Desde tu computador, en una carpeta vacía:

```bash
git clone https://github.com/TU_USUARIO/onboarding-report.git
cd onboarding-report

# Copiar estos archivos a la carpeta:
# - payana_reporte_unificado.html
# - update_report.py
# - .github/workflows/weekly_report.yml

mkdir -p .github/workflows
# (pegar los archivos)

git add .
git commit -m "Setup automatización reporte onboarding"
git push origin main
```

---

## Paso 3 — Agregar los Secrets en GitHub

Ve a tu repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Agrega estos 3 secrets:

| Nombre | Valor |
|--------|-------|
| `POSTHOG_API_KEY` | Tu PostHog API key (la nueva, rotada) |
| `POSTHOG_PROJECT` | `260911` |
| `SLACK_WEBHOOK_URL` | Tu Slack webhook URL (el nuevo, rotado) |

---

## Paso 4 — Activar GitHub Pages

1. Ve a tu repo → **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `gh-pages` / `/ (root)`
4. Guardar

La URL del reporte será:
```
https://TU_USUARIO.github.io/onboarding-report/
```

---

## Paso 5 — Probar manualmente

Antes de esperar al lunes:

1. Ve a tu repo → **Actions** → **Weekly Onboarding Report**
2. Click en **Run workflow** → **Run workflow**
3. Espera ~2 minutos
4. Si todo salió verde ✅, el reporte estará publicado y Slack recibirá la notificación

---

## Paso 6 — Actualizar la URL en el script (opcional)

En `update_report.py`, línea 74:
```python
report_url = "https://payana-io.github.io/onboarding-report/"
```
Cambia por tu URL real de GitHub Pages.

---

## Cómo funciona cada lunes

```
9:00am Colombia
    ↓
GitHub Actions corre update_report.py
    ↓
Hace 4 queries a PostHog:
  SELECT * FROM OnBoarding_Blocks
  SELECT * FROM OnBoarding_TableroControl
  SELECT * FROM OnBoarding_ForecastM1
  SELECT * FROM OnBoarding_ControlFacturacion
    ↓
Inyecta los datos en payana_reporte_unificado.html
    ↓
Publica index.html en GitHub Pages
    ↓
Slack recibe: "📊 Reporte Onboarding — 06 Abr 2026" + KPIs + botón
```

---

## Solución de problemas

**El workflow falla en "Fetching views"**
→ Verifica que el `POSTHOG_API_KEY` tenga permisos de lectura en el proyecto 260911

**El workflow falla en "Deploy to GitHub Pages"**
→ Ve a Settings → Actions → General → Workflow permissions → selecciona "Read and write permissions"

**No llega el mensaje a Slack**
→ Prueba el webhook manualmente:
```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"test"}' \
  TU_SLACK_WEBHOOK_URL
```

**El reporte muestra datos viejos**
→ Revisa que la plantilla `payana_reporte_unificado.html` tenga exactamente `const P_DATA = [...]` etc. (el script busca ese patrón exacto)
