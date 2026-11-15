import copy
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

import scraper


class MissingMetadataTests(unittest.TestCase):
    def setUp(self):
        self.row = json.loads((Path(__file__).parent / 'fixtures/missing_dimension.json').read_text())

    def test_actual_trade_please_with_empty_condition_is_preserved(self):
        variants = scraper.parse_variants(self.row)
        original = next(v for v in variants if v['IdProduct'] == 1971737)
        self.assertEqual(original['Condition'], '')
        self.assertEqual(original['Language'], 'Japanese')
        price = next(p for p in scraper.price_rows({'UNP0170': self.row}) if p['ID variante'] == '1971737')
        self.assertEqual(price['Condizione (codice sito)'], '')
        self.assertFalse(price['Condizione specificata dal sito'])
        self.assertTrue(price['Lingua specificata dal sito'])
        self.assertEqual(price['Stato'], 'VALUTAZIONE SU RICHIESTA')

    def test_blank_null_and_missing_dimensions_never_invent_labels(self):
        original = json.loads(self.row['product']['Variants'])[0]
        for missing in ('', None, 'absent'):
            with self.subTest(missing=missing):
                variant = copy.deepcopy(original)
                for field in scraper.VARIANT_DIMENSIONS:
                    if missing == 'absent':
                        variant.pop(field, None)
                    else:
                        variant[field] = missing
                self.row['product']['Variants'] = [variant]
                price = scraper.price_rows({'card': self.row})[0]
                self.assertEqual(price['Lingua'], '')
                self.assertEqual(price['Condizione (codice sito)'], '')
                self.assertEqual(scraper.parse_variants(self.row)[0], variant)
                self.assertFalse(price['Lingua specificata dal sito'])


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.row = json.loads((Path(__file__).parent / 'fixtures/variants.json').read_text())[0]
        self.header = {'version': '5.1.0', 'url': scraper.URL,
                       'ricerche': [{'id': None, 'name': 'Generale'}, {'id': 2364, 'name': 'Base'}]}

    def load(self, lines, tail=b''):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.jsonl'
            content = b''.join((json.dumps(line, ensure_ascii=False) + '\n').encode() for line in lines) + tail
            path.write_bytes(content)
            return scraper.load_checkpoint(path), content

    def test_completed_search_is_skippable_and_incomplete_search_is_not(self):
        result, _ = self.load([self.header, {'righe': [self.row], 'pagina': {'Pagina': 1}},
                               {'ricerca_completata': {'ID espansione': None, 'Stato': 'COMPLETATA'}}])
        self.assertEqual(len(result['records']), 1)
        self.assertIn(scraper.query_key(None), result['completed'])
        self.assertNotIn(scraper.query_key(2364), result['completed'])

    def test_latest_product_snapshot_is_used_without_duplicate_prices(self):
        new = copy.deepcopy(self.row)
        variants = json.loads(new['product']['Variants'])
        variants[0]['BuyList'] = 1.23
        new['product']['Variants'] = json.dumps(variants)
        result, _ = self.load([self.header, {'righe': [self.row]}, {'righe': [new]}])
        actual = next(iter(result['records'].values()))
        self.assertEqual(len(scraper.parse_variants(actual)), len(variants))
        self.assertEqual(scraper.parse_variants(actual)[0]['BuyList'], 1.23)

    def test_truncated_tail_is_dropped_and_search_is_repeated(self):
        result, data = self.load([self.header, {'righe': [self.row]}], tail=b'{"ricerca_completata":')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['completed'], {})
        self.assertLess(result['valid_bytes'], len(data))

    def test_corrupt_middle_line_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.load([self.header], tail=b'not json\n{}\n')

    def test_wrong_source_and_unknown_completion_are_rejected(self):
        bad = dict(self.header, url='https://example.com')
        with self.assertRaises(RuntimeError):
            self.load([bad])
        with self.assertRaises(RuntimeError):
            self.load([self.header, {'ricerca_completata': {'ID espansione': 999, 'Stato': 'COMPLETATA'}}])

    def check_scrape_resume(self, live=False):
        editions = [{'id': 2364, 'name': 'Base'}]
        general = dict(page=1, pages=1, pending=0, only_hot=False, filters={},
                       no_results=False, editions=editions, rows=[self.row], counters={})
        new = copy.deepcopy(self.row)
        new['product']['Id'] = 'NEW-CARD'
        new['name'] = 'Nuova carta'
        base = dict(general, filters={'edition': 2364}, rows=[new])
        page = MagicMock()
        page.evaluate.return_value = {'editions': editions}
        page.locator.return_value.evaluate.return_value = 'number:2364'
        browser = MagicMock()
        browser.new_page.return_value = page
        playwright_context = MagicMock()
        playwright_context.__enter__.return_value.chromium.launch.return_value = browser
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.jsonl'
            lines = [self.header, {'righe': [self.row]},
                     {'ricerca_completata': {'ID espansione': None, 'Espansione': 'Ricerca generale', 'Stato': 'COMPLETATA'}}]
            original = ''.join(json.dumps(line) + '\n' for line in lines)
            source.write_text(original)
            output = Path(directory) / 'result.xlsx'
            with patch.object(scraper, 'sync_playwright', return_value=playwright_context), \
                 patch.object(scraper, 'wait_ready', side_effect=[general, base]) as waiting, \
                 patch.object(scraper, 'save_excel') as saving:
                scraper.scrape(output, resume_checkpoint=source, live_sheets=live)
            self.assertEqual(waiting.call_count, 2)  # Bootstrap, poi solo Base.
            self.assertEqual(waiting.call_args.kwargs['edition'], 2364)
            self.assertTrue(saving.call_args.args[3])
            self.assertEqual(len(saving.call_args.args[1]), 2)
            self.assertEqual(source.read_text(), original)
            recovered = scraper.load_checkpoint(output.with_suffix('.checkpoint.jsonl'))
            self.assertEqual(len(recovered['records']), 2)
            self.assertEqual(len(recovered['completed']), 2)
            if live:
                status = json.loads(output.with_suffix('.live.status.json').read_text())
                self.assertEqual(status['state'], 'COMPLETO')
                self.assertEqual(status['cards'], 2)

    def test_scrape_resumes_and_new_checkpoint_remains_self_contained(self):
        self.check_scrape_resume()

    def test_scrape_and_background_backup_complete_together(self):
        from test_live_sheets import LiveMemory
        from sheets_sync import SheetsClient
        memory = LiveMemory()
        with patch('live_sheets.authenticated_session', return_value=memory), \
             patch('live_sheets.SheetsClient', side_effect=lambda ident, session: SheetsClient(ident, session, interval=0)):
            self.check_scrape_resume(live=True)


if __name__ == '__main__':
    unittest.main()
