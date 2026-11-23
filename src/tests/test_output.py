import unittest
from dataclasses import replace
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from tupa.output import render, render_html
from tupa.sources import DEFAULT_SOURCES
from tupa.version import get_version


class OutputTests(unittest.TestCase):
    def test_render_json(self):
        payload = render({"updated": "now", "members": {}}, indent=None)
        self.assertEqual(payload, b'{"updated": "now", "members": {}}\n')

    def test_render_html_escapes_content(self):
        payload = render_html(
            {
                "updated": "now",
                "members": {
                    "<script>": {
                        "updated": "then",
                        "status": "warn",
                        "comment": "<b>&co</b>",
                    }
                },
            },
            sources=[],
        )
        self.assertIn(
            "TUPA⚡: Telemetría Unificada de Potencia Argentina".encode(), payload
        )
        self.assertIn("<h1>TUPA⚡</h1>".encode(), payload)
        self.assertIn("Telemetría Unificada de Potencia Argentina".encode(), payload)
        self.assertIn(b"&lt;script&gt;", payload)
        self.assertIn(b"&lt;b&gt;&amp;co&lt;/b&gt;", payload)
        self.assertIn(b"card-warn", payload)
        self.assertIn("<b>1</b> Atención".encode(), payload)
        self.assertIn(b"<b>1</b> Total", payload)
        self.assertIn("Actualizado".encode(), payload)
        self.assertIn(f'class="version">v{get_version()}'.encode(), payload)

    def test_render_html_sources_details(self):
        sources = [
            DEFAULT_SOURCES["cammesa_edenor"],
            DEFAULT_SOURCES["enre_edenor"],
        ]
        payload = render_html({"updated": "now", "members": {}}, sources=sources)
        self.assertIn(b'<details class="sources">', payload)
        self.assertIn(b"<summary>Lista de fuentes</summary>", payload)
        self.assertNotIn(b'id="sources-button"', payload)
        self.assertNotIn(b"showModal()", payload)
        for heading in ("Nombre", "API", "Documentación"):
            self.assertIn(f"<th>{heading}</th>".encode(), payload)
        self.assertIn(b"CAMMESA Edenor (1077)", payload)
        self.assertIn(b"ENRE Edenor", payload)
        self.assertIn(DEFAULT_SOURCES["enre_edenor"].docs_url.encode(), payload)
        self.assertNotIn(b"ENRE Edesur", payload)
        self.assertNotIn(b"SACME", payload)

    def test_render_html_without_members(self):
        payload = render_html({"updated": "now", "members": {}}, sources=[])
        self.assertIn("No hay miembros configurados.".encode(), payload)
        self.assertIn("Sin fuentes activas.".encode(), payload)

    def test_render_html_rejects_unsafe_source_urls(self):
        sources = [
            replace(DEFAULT_SOURCES["enre_edenor"], url="javascript:alert(1)")
        ]
        payload = render_html({"updated": "now", "members": {}}, sources=sources)
        self.assertIn(b'href=""', payload)
        self.assertNotIn(b'href="javascript:', payload)

    def test_render_html_csp_has_no_script(self):
        payload = render_html({"updated": "now", "members": {}}, sources=[]).decode("utf-8")
        self.assertIn(
            "default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'",
            payload,
        )
        self.assertNotIn("<script", payload)
        self.assertNotIn("sha256-", payload)


class VersionTests(unittest.TestCase):
    def test_uses_installed_metadata(self):
        with patch("tupa.version.version", return_value="9.9.9"):
            self.assertEqual(get_version(), "9.9.9")

    def test_falls_back_to_pyproject(self):
        with patch("tupa.version.version", side_effect=PackageNotFoundError):
            self.assertRegex(get_version(), r"\d+\.\d+\.\d+")
