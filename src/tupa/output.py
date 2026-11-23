from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Sequence

from .model import STATUS_ORDER, STATUSES, SourceSpec
from .version import get_version

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
      .sources {
        margin: 0 auto;
        max-width: 880px;
        color: var(--muted);
        font-size: 0.78rem;
      }
      .sources summary {
        width: fit-content;
        padding: 6px 12px;
        border: 1px solid var(--border);
        border-radius: 999px;
        color: var(--text);
        cursor: pointer;
      }
      .sources summary:hover { border-color: #8ba4ff; }
      .sources table { margin-top: 12px; }
      .table-scroll { overflow: auto; }
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

def _sources_table(specs: Sequence[SourceSpec]) -> str:
    rows = [(spec.label, spec.url, spec.docs_url) for spec in specs]
    body = "".join(
        f'          <tr><td>{_escape(name)}</td>'
        f'<td><a href="{_escape(_safe_href(api))}" target="_blank" rel="noreferrer">{_escape(api or "—")}</a></td>'
        f'<td><a href="{_escape(_safe_href(docs))}" target="_blank" rel="noreferrer">Documentación</a></td></tr>\n'
        for name, api, docs in rows
    )
    if not body:
        body = '          <tr><td colspan="3">Sin fuentes activas.</td></tr>\n'
    return (
        "      <table>\n"
        "        <thead><tr><th>Nombre</th><th>API</th><th>Documentación</th></tr></thead>\n"
        "        <tbody>\n"
        f"{body}"
        "        </tbody>\n"
        "      </table>\n"
    )

_CSP = "default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _safe_href(value: object) -> str:
    text = str(value)
    return text if re.match(r"https?://", text, re.IGNORECASE) else ""


def _state(value: object) -> str:
    text = str(value)
    return text if text in STATUSES else "unknown"


def render(status: dict) -> bytes:
    return (
        json.dumps(status, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8", errors="replace")


def render_html(status: dict, *, sources: Sequence[SourceSpec]) -> bytes:
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
        f'  <meta http-equiv="Content-Security-Policy" content="{_CSP}">\n'
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
        '      <details class="sources">\n'
        '        <summary>Lista de fuentes</summary>\n'
        '        <div class="table-scroll">\n'
        f"{_sources_table(sources)}"
        '        </div>\n'
        '      </details>\n'
        f'      <span class="version">v{version}</span>\n'
        '      <a href="/api">API JSON</a>\n'
        '    </footer>\n'
        '  </div>\n'
        "</body>\n"

        "</html>\n"
    ).encode("utf-8", errors="replace")
