from __future__ import annotations

import html
import json
from collections import Counter

from .model import STATUSES
from .version import get_version

STATUS_ORDER = ("operational", "warn", "high", "disaster", "unknown")

STATUS_LABELS = {
    "operational": "Operativo",
    "warn": "Atención",
    "high": "Alto",
    "disaster": "Crítico",
    "unknown": "Desconocido",
}

_STYLE = """    <style>
      :root {
        color-scheme: dark;
        --bg: #0b1020;
        --panel: #151d34;
        --border: #2b3858;
        --text: #eef2fb;
        --muted: #9aa8c7;
        --operational: #34d399;
        --warn: #fbbf24;
        --high: #fb923c;
        --disaster: #f87171;
        --unknown: #94a3b8;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        min-height: 100vh;
        font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
        background:
          radial-gradient(circle at 15% 0%, rgba(99, 102, 241, 0.18), transparent 38rem),
          radial-gradient(circle at 85% 10%, rgba(16, 185, 129, 0.12), transparent 32rem),
          var(--bg);
        color: var(--text);
      }
      .wrap { max-width: 1080px; margin: 0 auto; padding: 48px 24px 32px; }
      .masthead {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 24px;
        margin-bottom: 32px;
      }
      .eyebrow {
        margin: 0 0 8px;
        color: #8ba4ff;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }
      h1 {
        margin: 0;
        font-size: clamp(2.25rem, 6vw, 3.5rem);
        letter-spacing: -0.05em;
        line-height: 1;
      }
      .subtitle { margin: 12px 0 0; color: var(--muted); font-size: 1rem; }
      .snapshot { text-align: right; color: var(--muted); font-size: 0.85rem; }
      .snapshot time {
        display: block;
        margin-top: 4px;
        color: var(--text);
        font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
        font-size: 0.95rem;
      }
      .summary { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 28px; }
      .counter {
        --state: var(--unknown);
        min-width: 104px;
        padding: 10px 14px;
        border: 1px solid var(--border);
        border-left: 4px solid var(--state);
        border-radius: 12px;
        background: rgba(21, 29, 52, 0.82);
        color: var(--muted);
        font-size: 0.8rem;
      }
      .counter b { color: var(--text); font-size: 1.25rem; margin-right: 6px; }
      .counter-operational { --state: var(--operational); }
      .counter-warn { --state: var(--warn); }
      .counter-high { --state: var(--high); }
      .counter-disaster { --state: var(--disaster); }
      .counter-unknown { --state: var(--unknown); }
      .counter-total {
        --state: #ffffff;
        color: var(--text);
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        gap: 18px;
      }
      .card {
        --state: var(--unknown);
        position: relative;
        overflow: hidden;
        padding: 22px;
        border: 1px solid var(--border);
        border-radius: 18px;
        background: linear-gradient(160deg, rgba(31, 41, 68, 0.95), rgba(17, 24, 43, 0.95));
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.22);
      }
      .card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 4px;
        background: var(--state);
      }
      .card-operational { --state: var(--operational); }
      .card-warn { --state: var(--warn); }
      .card-high { --state: var(--high); }
      .card-disaster { --state: var(--disaster); }
      .card-unknown { --state: var(--unknown); }
      .card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
      h2 { margin: 0; font-size: 1.25rem; letter-spacing: -0.02em; }
      .pill {
        padding: 5px 11px;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }
      .pill-operational { background: rgba(52, 211, 153, 0.14); color: #6ee7b7; }
      .pill-warn { background: rgba(251, 191, 36, 0.14); color: #fcd34d; }
      .pill-high { background: rgba(251, 146, 60, 0.14); color: #fdba74; }
      .pill-disaster { background: rgba(248, 113, 113, 0.14); color: #fca5a5; }
      .pill-unknown { background: rgba(148, 163, 184, 0.14); color: #cbd5e1; }
      .updated { margin: 18px 0 0; color: var(--muted); font-size: 0.8rem; }
      .updated time {
        color: var(--text);
        font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
      }
      .comment {
        margin: 14px 0 0;
        padding-top: 14px;
        border-top: 1px solid var(--border);
        color: #c4cfe6;
        font-size: 0.87rem;
        line-height: 1.6;
        overflow-wrap: anywhere;
      }
      .empty { grid-column: 1 / -1; color: var(--muted); }
      footer {
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        gap: 16px;
        margin-top: 32px;
        padding-top: 20px;
        border-top: 1px solid var(--border);
        color: var(--muted);
        font-size: 0.8rem;
      }
      .version {
        font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
      }
      a { color: #8ba4ff; text-decoration: none; }
      a:hover { text-decoration: underline; }
      .sources-button, .dialog-ok {
        padding: 6px 12px;
        border: 1px solid var(--border);
        border-radius: 999px;
        background: rgba(139, 164, 255, 0.12);
        color: var(--text);
        cursor: pointer;
        font: inherit;
        font-size: 0.78rem;
      }
      .sources-button:hover, .dialog-ok:hover { border-color: #8ba4ff; }
      .sources-dialog {
        width: min(880px, calc(100% - 32px));
        max-height: min(80vh, 720px);
        padding: 0;
        border: 1px solid var(--border);
        border-radius: 18px;
        background: #111a2e;
        color: var(--text);
        box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
      }
      .sources-dialog::backdrop {
        background: rgba(5, 8, 18, 0.72);
        backdrop-filter: blur(3px);
      }
      .dialog-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 18px 22px;
        border-bottom: 1px solid var(--border);
      }
      .dialog-head h2 { font-size: 1.1rem; }
      .table-scroll { max-height: calc(80vh - 80px); overflow: auto; }
      table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
      th, td {
        padding: 11px 16px;
        border-bottom: 1px solid var(--border);
        text-align: left;
        vertical-align: top;
      }
      th {
        position: sticky;
        top: 0;
        background: #16203a;
        color: var(--muted);
        font-size: 0.7rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      td a { overflow-wrap: anywhere; }
      @media (max-width: 640px) {
        .wrap { padding: 32px 16px 24px; }
        .masthead { align-items: flex-start; flex-direction: column; }
        .snapshot { text-align: left; }
      }
    </style>"""

_CAMMESA_DOCS = "https://cammesaweb.cammesa.com/informes-y-estadisticas/"
_ENRE_DOCS = "https://www.enre.gov.ar/mapacortes/index.html"

_SOURCE_ROWS = (
    (
        "CAMMESA Edenor (1077)",
        "https://api.cammesa.com/demanda-svc/demanda/ObtieneDemandaYTemperaturaRegion?id_region=1077",
        _CAMMESA_DOCS,
    ),
    (
        "CAMMESA Edesur (1078)",
        "https://api.cammesa.com/demanda-svc/demanda/ObtieneDemandaYTemperaturaRegion?id_region=1078",
        _CAMMESA_DOCS,
    ),
    (
        "ENRE Edenor",
        "https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
        _ENRE_DOCS,
    ),
    (
        "ENRE Edesur",
        "https://www.enre.gov.ar/paginacorte/js/data_EDS.js",
        _ENRE_DOCS,
    ),
    ("SADI", "https://cammesaweb.cammesa.com/", _CAMMESA_DOCS),
    ("CAMMESA", "https://cammesaweb.cammesa.com/", _CAMMESA_DOCS),
    ("Transener", "https://cammesaweb.cammesa.com/", "https://www.enre.gov.ar/"),
    ("SACME", "https://test.sacme.com.ar/", "https://www.sacme.com.ar/about-us/"),
    (
        "Edenor",
        "https://www.enre.gov.ar/paginacorte/tabla_cortes_edenor.html",
        _ENRE_DOCS,
    ),
    (
        "Edesur",
        "https://www.enre.gov.ar/paginacorte/tabla_cortes_edesur.html",
        _ENRE_DOCS,
    ),
    ("Edelap", "https://oceba.gba.gov.ar/", "https://oceba.gba.gov.ar/"),
    (
        "CortesDeLuz.com.ar (CDL)",
        "https://live.cortesdeluz.com.ar/current.json",
        "https://cortesdeluz.com.ar/openapi.json",
    ),
)

_SOURCES_TABLE = (
    "      <table>\n"
    "        <thead><tr><th>Nombre</th><th>API</th><th>Documentación</th></tr></thead>\n"
    "        <tbody>\n"
    + "".join(
        f'          <tr><td>{name}</td>'
        f'<td><a href="{api}" target="_blank" rel="noreferrer">API</a></td>'
        f'<td><a href="{docs}" target="_blank" rel="noreferrer">Documentación</a></td></tr>\n'
        for name, api, docs in _SOURCE_ROWS
    )
    + "        </tbody>\n"
    "      </table>\n"
)

_SCRIPT = """    <script>
      const sourcesButton = document.getElementById("sources-button");
      const sourcesDialog = document.getElementById("sources-dialog");
      sourcesButton.addEventListener("click", () => sourcesDialog.showModal());
      sourcesDialog.addEventListener("close", () => sourcesButton.focus());
    </script>"""


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _state(value: object) -> str:
    text = str(value)
    return text if text in STATUSES else "unknown"


def render(status: dict, *, indent: int | None = 2) -> bytes:
    return (json.dumps(status, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")


def render_html(status: dict) -> bytes:
    members = status.get("members", {})
    states = [_state(member.get("status", "unknown")) for member in members.values()]
    counts = Counter(states)
    summary = "".join(
        f'<span class="counter counter-{state}"><b>{counts.get(state, 0)}</b> '
        f"{STATUS_LABELS[state]}</span>"
        for state in STATUS_ORDER
    )
    summary += f'<span class="counter counter-total"><b>{len(members)}</b> Total</span>'
    cards = []
    for name, member in members.items():
        state = _state(member.get("status", "unknown"))
        updated = _escape(member.get("updated", ""))
        comment = member.get("comment")
        comment_html = (
            f'        <p class="comment">{_escape(comment)}</p>\n' if comment else ""
        )
        cards.append(
            f'      <article class="card card-{state}">\n'
            f'        <div class="card-head">\n'
            f"          <h2>{_escape(name)}</h2>\n"
            f'          <span class="pill pill-{state}">{STATUS_LABELS[state]}</span>\n'
            f"        </div>\n"
            f'        <p class="updated">Actualizado <time datetime="{updated}">{updated}</time></p>\n'
            f"{comment_html}"
            f"      </article>"
        )
    if not cards:
        cards.append('      <p class="empty">No hay miembros configurados.</p>')
    card_html = "\n".join(cards)
    updated = _escape(status.get("updated", ""))
    version = _escape(get_version())
    return (
        "<!doctype html>\n"
        '<html lang="es-AR">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "  <title>TUPA⚡: Telemetría Unificada de Potencia Argentina</title>\n"
        f"{_STYLE}\n"
        "</head>\n"
        "<body>\n"
        '  <div class="wrap">\n'
        '    <header class="masthead">\n'
        "      <div>\n"
        '        <p class="eyebrow">Telemetría Unificada de Potencia Argentina</p>\n'
        "        <h1>TUPA⚡</h1>\n"
        '        <p class="subtitle">Estado de la red eléctrica argentina de un vistazo.</p>\n'
        "      </div>\n"
        f'      <div class="snapshot">Captura<time datetime="{updated}">{updated}</time></div>\n'
        "    </header>\n"
        '    <section class="summary" aria-label="Resumen de estados">\n'
        f"{summary}\n"
        "    </section>\n"
        '    <main class="grid">\n'
        f"{card_html}\n"
        "    </main>\n"
        "    <footer>\n"
        '      <span>Los datos son agregados; verificá las decisiones críticas con las fuentes primarias.</span>\n'
        '      <button type="button" class="sources-button" id="sources-button" aria-haspopup="dialog">Lista de fuentes</button>\n'
        f'      <span class="version">v{version}</span>\n'
        '      <a href="/api">API JSON</a>\n'
        '    </footer>\n'
        '  </div>\n'
        '  <dialog id="sources-dialog" class="sources-dialog" aria-labelledby="sources-title">\n'
        '    <div class="dialog-head">\n'
        '      <h2 id="sources-title">Lista de fuentes</h2>\n'
        '      <form method="dialog"><button class="dialog-ok" value="OK">OK</button></form>\n'
        '    </div>\n'
        '    <div class="table-scroll">\n'
        f"{_SOURCES_TABLE}"
        '    </div>\n'
        '  </dialog>\n'
        f"{_SCRIPT}\n"
        "</body>\n"

        "</html>\n"
    ).encode("utf-8")
