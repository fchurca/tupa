import unittest

from tupa.output import render, render_html
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
            "TUPA: Telemetría Unificada de Potencia Argentina".encode(), payload
        )
        self.assertIn("Telemetría Unificada de Potencia Argentina".encode(), payload)
        self.assertIn(b"&lt;script&gt;", payload)
        self.assertIn(b"&lt;b&gt;&amp;co&lt;/b&gt;", payload)
        self.assertIn(b"card-warn", payload)
        self.assertIn("<b>1</b> Atención".encode(), payload)
        self.assertIn(b"<b>1</b> Total", payload)
        self.assertIn("Actualizado".encode(), payload)
        self.assertIn(f'class="version">v{get_version()}'.encode(), payload)

    def test_render_html_without_members(self):
        payload = render_html({"updated": "now", "members": {}})
        self.assertIn("No hay miembros configurados.".encode(), payload)
