import csv
import functools
import http.server
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import openpyxl

import esporta_json

ROOT = Path(__file__).resolve().parent.parent


def row(n, name, language='Italiano', status='PREZZO DISPONIBILE', buylist='1.5', credit='3', foil='', first=''):
    return {'entry_key': f'E{n:05d}', 'card_key': f'C{n}', 'name': name, 'name_en': None, 'expansion': 'Base',
            'serial_number': str(n), 'image': '', 'language': language, 'condition': 'NM/M', 'foil': foil,
            'first_edition': first, 'buylist': buylist, 'credit': credit, 'status': status}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def browser_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@unittest.skipUnless(shutil.which('node'), 'Node.js non disponibile')
class FilterLogicTests(unittest.TestCase):
    def test_filters_sorting_and_options(self):
        result = subprocess.run(['node', str(ROOT / 'tests/js/test_filtri.js')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(browser_available(), 'Chromium non disponibile')
class PageTests(unittest.TestCase):
    """Apre davvero docs/index.html in un browser con dati finti generati come in produzione."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.tmp = tempfile.TemporaryDirectory()
        rows = [row(n, f'Carta {n:03d}', language='English' if n % 2 else 'Italiano', credit=str(n / 10 + 0.1),
                    buylist=str(n / 20 + 0.1)) for n in range(1, 121)]
        rows[0]['name'] = 'Pok\u00e9mon Speciale'
        rows[1]['foil'] = 'Reverse Holo'
        rows += [row(500, 'Zubat Richiesta', status='VALUTAZIONE SU RICHIESTA', buylist=None, credit=None),
                 row(501, 'Abra Senza Prezzo', status='SENZA PREZZO BUYLIST', buylist='0', credit='0')]
        groups = esporta_json.build_groups(rows)
        esporta_json.write_site(groups, rows, None, ROOT / 'docs', Path(cls.tmp.name, 'sito'))
        handler = functools.partial(QuietHandler, directory=str(Path(cls.tmp.name, 'sito')))
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/index.html'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def open_page(self):
        context = self.browser.new_context(accept_downloads=True, locale='it-IT')
        page = context.new_page()
        self.addCleanup(context.close)
        self.errors = []
        page.on('pageerror', lambda e: self.errors.append(str(e)))
        page.goto(self.url)
        page.wait_for_function("document.getElementById('info').textContent.startsWith('Risultati')")
        return page

    def wait_info(self, page, text):
        page.wait_for_function('t => document.getElementById("info").textContent === t', arg=text, timeout=10000)

    def test_default_view_matches_the_original_site(self):
        page = self.open_page()
        self.assertEqual(page.inner_text('#info'), 'Risultati 1\u201350 di 120')
        self.assertEqual(page.inner_text('#pageInfo'), 'Pagina 1 di 3')
        self.assertEqual(page.input_value('#minCash'), '0.01')
        self.assertTrue(page.is_checked('input[name=status][value="PREZZO DISPONIBILE"]'))
        self.assertEqual(page.locator('#rows tr').count(), 50)
        self.assertIn('Dati aggiornati il', page.inner_text('#metaLine'))
        self.assertEqual(self.errors, [])
        # Buono decrescente: la prima riga ha il credito piu alto (12,10)
        self.assertEqual(page.locator('#rows tr').first.locator('td').nth(9).inner_text(), '12,10')

    def test_filter_checkboxes_come_from_the_data(self):
        page = self.open_page()
        languages = page.eval_on_selector_all('input[name=language]', 'els => els.map(e => e.value)')
        self.assertEqual(languages, ['Italiano', 'English'])
        self.assertEqual(page.eval_on_selector_all('input[name=foil]', 'els => els.map(e => e.value)'), ['Reverse Holo'])

    def test_live_filters_and_accent_insensitive_search(self):
        page = self.open_page()
        page.fill('#name', 'pokemon')
        self.wait_info(page, 'Risultati 1\u20131 di 1')
        page.fill('#name', '')
        page.check('input[name=language][value=English]')
        self.wait_info(page, 'Risultati 1\u201350 di 60')

    def test_other_statuses_are_loaded_only_when_requested(self):
        page = self.open_page()
        requested = []
        page.on('request', lambda r: requested.append(r.url.rsplit('/', 1)[-1]))
        page.fill('#minCash', ''); page.fill('#minBuono', '')
        page.check('input[name=status][value="VALUTAZIONE SU RICHIESTA"]')
        self.wait_info(page, 'Risultati 1\u201350 di 121')
        self.assertIn('richiesta.json', requested)
        self.assertNotIn('senza-prezzo.json', requested)

    def test_pagination_and_page_size(self):
        page = self.open_page()
        page.click('#next')
        self.assertEqual(page.inner_text('#pageInfo'), 'Pagina 2 di 3')
        self.assertEqual(page.inner_text('#info'), 'Risultati 51\u2013100 di 120')
        page.click('#btnSettings')
        page.select_option('#optPageSize', '100')
        self.assertEqual(page.inner_text('#pageInfo'), 'Pagina 1 di 2')

    def test_export_contains_every_page_of_the_current_search(self):
        page = self.open_page()
        page.check('input[name=language][value=English]')
        self.wait_info(page, 'Risultati 1\u201350 di 60')
        with page.expect_download() as download:
            page.click('#btnCsv')
        out = Path(self.tmp.name, 'ricerca.csv')
        download.value.save_as(out)
        with out.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=';'))
        self.assertEqual(len(rows) - 1, 60)               # tutte le pagine, non solo le 50 visibili
        self.assertEqual({r[4] for r in rows[1:]}, {'English'})
        with page.expect_download() as download:
            page.click('#btnXlsx')
        download.value.save_as(Path(self.tmp.name, 'ricerca.xlsx'))
        book = openpyxl.load_workbook(Path(self.tmp.name, 'ricerca.xlsx'))
        self.assertEqual(book['Buylist'].max_row - 1, 60)
        info = [c.value for c in book['Informazioni']['A'] if c.value]
        self.assertIn('Lingua', info)

    def test_dark_theme_and_page_size_are_remembered(self):
        page = self.open_page()
        page.click('#btnSettings')
        page.check('#optDark')
        page.select_option('#optPageSize', '25')
        page.reload()
        page.wait_for_function("document.getElementById('info').textContent.startsWith('Risultati')")
        self.assertTrue(page.evaluate("document.body.classList.contains('dark')"))
        self.assertEqual(page.input_value('#optPageSize'), '25')

    def test_missing_data_shows_a_clear_error(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.route('**/data/meta.json', lambda route: route.fulfill(status=404, body='no'))
        page.goto(self.url)
        page.wait_for_function("document.getElementById('info').textContent.startsWith('Errore')")
        self.assertIn('impossibile leggere i dati', page.inner_text('#info'))


if __name__ == '__main__':
    unittest.main()
