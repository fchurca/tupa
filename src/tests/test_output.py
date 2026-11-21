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
        payload = render_html({"updated": "now", "members": {}})
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
        for name in (
            "CAMMESA Edenor (1077)",
            "CAMMESA Edesur (1078)",
            "ENRE Edenor",
            "ENRE Edesur",
            "SADI",
            "CAMMESA",
            "Transener",
            "SACME",
            "Edenor",
            "Edesur",
            "Edelap",
            "CortesDeLuz.com.ar (CDL)",
        ):
            self.assertIn(f"<td>{name}</td>".encode(), payload)
        for url in (
            "https://api.cammesa.com/demanda-svc/demanda/ObtieneDemandaYTemperaturaRegion?id_region=1077",
            "https://api.cammesa.com/demanda-svc/demanda/ObtieneDemandaYTemperaturaRegion?id_region=1078",
            "https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
            "https://www.enre.gov.ar/paginacorte/js/data_EDS.js",
            "https://cammesaweb.cammesa.com/informes-y-estadisticas/",
            "https://www.enre.gov.ar/mapacortes/index.html",
            "https://cortesdeluz.com.ar/openapi.json",
        ):
            self.assertIn(url.encode(), payload)

    def test_render_html_without_members(self):
        payload = render_html({"updated": "now", "members": {}})
        self.assertIn("No hay miembros configurados.".encode(), payload)
