import base64
import hashlib
import re
import unittest
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from tupa.model import SourceSpec
from tupa.output import _SCRIPT_HASH, render, render_html
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
            }
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

    def test_render_html_sources_dialog(self):
        sources = [
            SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077),
            SourceSpec(
                "enre_edenor",
                "enre_outages",
                "edenor",
                url="https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
                empresa="EDENOR",
            ),
        ]
        payload = render_html({"updated": "now", "members": {}}, sources=sources)
        self.assertIn(b'id="sources-button"', payload)
        self.assertIn("Lista de fuentes".encode(), payload)
        self.assertIn(b'id="sources-dialog"', payload)
        self.assertIn(
            b'<form method="dialog"><button class="dialog-ok" value="OK">OK</button></form>',
            payload,
        )
        self.assertIn(b"showModal()", payload)
        for heading in ("Nombre", "API", "Documentación"):
            self.assertIn(f"<th>{heading}</th>".encode(), payload)
        self.assertIn(b"CAMMESA Edenor (1077)", payload)
        self.assertIn(b"ENRE Edenor", payload)
        self.assertNotIn(b"ENRE Edesur", payload)
        self.assertNotIn(b"SACME", payload)

    def test_render_html_without_members(self):
        payload = render_html({"updated": "now", "members": {}}, sources=[])
        self.assertIn("No hay miembros configurados.".encode(), payload)
        self.assertIn("Sin fuentes activas.".encode(), payload)

    def test_render_html_rejects_unsafe_source_urls(self):
        sources = [
            SourceSpec(
                "evil", "enre_outages", "edenor", url="javascript:alert(1)", empresa="EDENOR"
            )
        ]
        payload = render_html({"updated": "now", "members": {}}, sources=sources)
        self.assertIn(b'href=""', payload)
        self.assertNotIn(b'href="javascript:', payload)

    def test_render_html_csp_hash_matches_script(self):
        payload = render_html({"updated": "now", "members": {}}).decode("utf-8")
        script = re.search(r"<script>(.*?)</script>", payload, re.S)
        self.assertIsNotNone(script)
        digest = hashlib.sha256(script.group(1).encode("utf-8")).digest()
        expected = "sha256-" + base64.b64encode(digest).decode("ascii")
        self.assertEqual(expected, _SCRIPT_HASH)
        self.assertIn(f"script-src '{expected}'".encode(), payload.encode())


class VersionTests(unittest.TestCase):
    def test_uses_installed_metadata(self):
        with patch("tupa.version.version", return_value="9.9.9"):
            self.assertEqual(get_version(), "9.9.9")

    def test_falls_back_to_pyproject(self):
        with patch("tupa.version.version", side_effect=PackageNotFoundError):
            self.assertRegex(get_version(), r"\d+\.\d+\.\d+")
