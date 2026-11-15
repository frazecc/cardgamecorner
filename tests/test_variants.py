import copy
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook
import scraper


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((Path(__file__).parent / 'fixtures/variants.json').read_text())
        self.row = copy.deepcopy(self.rows[0])
        self.variants = scraper.parse_variants(self.row)

    def test_preserves_every_real_variant_and_price(self):
        actual = scraper.price_rows({'Id:BS0004': self.row})
        self.assertEqual(len(actual), len(self.variants))
        for original, exported in zip(self.variants, actual):
            self.assertEqual(exported['ID variante'], str(original['IdProduct']))
            self.assertEqual(exported['Lingua'], original['Language'])
            self.assertEqual(exported['Condizione (codice sito)'], original['Condition'])
            self.assertEqual(exported['Prezzo buylist (€)'], original['BuyList'])
            self.assertEqual(exported['Credito (€)'], original['Credit'])
        # Prezzo osservato anche cambiando lingua/condizione sul sito.
        first_sp = [v for v in actual if v['Lingua'] == 'Italiano' and
                    v['Condizione (codice sito)'] == 'SP' and
                    v['Prima edizione (dato sito)'] == 'First Edition']
        self.assertEqual(first_sp[0]['Prezzo buylist (€)'], 1879.95)
        self.assertEqual(first_sp[0]['Credito (€)'], 2519.95)

    def test_keeps_distinct_ids_for_same_language_and_condition(self):
        prices = scraper.price_rows({'card': self.row})
        matches = [p for p in prices if p['Lingua'] == 'English' and p['Condizione (codice sito)'] == 'NM/M']
        self.assertEqual(len(matches), 2)
        self.assertNotEqual(matches[0]['ID variante'], matches[1]['ID variante'])

    def test_merges_variants_across_searches(self):
        old = copy.deepcopy(self.row)
        old['product']['Variants'] = json.dumps(self.variants[:3])
        new = copy.deepcopy(self.row)
        new['product']['Variants'] = json.dumps(self.variants[3:])
        merged = scraper.merge_product_row(old, new)
        self.assertEqual(scraper.parse_variants(merged), self.variants)

    def test_updates_price_for_same_id_and_dimensions(self):
        new = copy.deepcopy(self.row)
        changed = copy.deepcopy(self.variants[:1])
        changed[0]['BuyList'] = 123.45
        new['product']['Variants'] = changed
        result = scraper.parse_variants(scraper.merge_product_row(self.row, new))
        self.assertEqual(len(result), len(self.variants))
        self.assertEqual(result[0]['BuyList'], 123.45)

    def test_does_not_merge_conflicting_dimensions(self):
        new = copy.deepcopy(self.row)
        changed = copy.deepcopy(self.variants[:1])
        changed[0]['Language'] = 'Japanese'
        new['product']['Variants'] = changed
        with self.assertRaisesRegex(RuntimeError, 'diversa'):
            scraper.merge_product_row(self.row, new)

    def test_bad_or_missing_variants_stop_export(self):
        for data in (None, '{broken', {}, [{'IdProduct': 1}]):
            with self.subTest(data=data):
                self.row['product']['Variants'] = data
                with self.assertRaises(RuntimeError):
                    scraper.parse_variants(self.row)

    def test_negative_nan_and_missing_prices_are_rejected(self):
        for price in (-1, float('nan'), None, True):
            with self.subTest(price=price):
                changed = copy.deepcopy(self.variants[:1])
                changed[0]['BuyList'] = price
                self.row['product']['Variants'] = changed
                with self.assertRaises(RuntimeError):
                    scraper.parse_variants(self.row)

    def test_keeps_evaluation_zero_and_excluded_rows(self):
        variant = copy.deepcopy(self.variants[0])
        variant.update(Evaluation=True, BuyList=0, Credit=0)
        self.row['product']['Variants'] = [variant]
        self.assertEqual(scraper.price_rows({'card': self.row})[0]['Stato'], 'VALUTAZIONE SU RICHIESTA')
        variant.update(Evaluation=False)
        self.assertEqual(scraper.price_rows({'card': self.row})[0]['Stato'], 'SENZA PREZZO BUYLIST')
        variant.update(NoBuyList=True, BuyList=10)
        self.assertEqual(scraper.price_rows({'card': self.row})[0]['Stato'], 'ESCLUSA DALLA BUYLIST')

    def test_does_not_invent_missing_combinations(self):
        self.row['product']['Variants'] = self.variants[:1]
        self.assertEqual(len(scraper.price_rows({'card': self.row})), 1)

    def test_reverse_and_first_edition_are_separate(self):
        variants = copy.deepcopy(self.variants[:2])
        variants[0]['Foil'] = 'Reverse Holo'
        variants[0]['FirstEdition'] = 'First Edition'
        variants[1]['Foil'] = ''
        variants[1]['FirstEdition'] = ''
        self.row['product']['Variants'] = variants
        prices = scraper.price_rows({'card': self.row})
        self.assertEqual(prices[0]['Foil (dato sito)'], 'Reverse Holo')
        self.assertEqual(prices[0]['Prima edizione (dato sito)'], 'First Edition')
        self.assertEqual(prices[1]['Foil (dato sito)'], '')

    def test_excel_round_trip_preserves_all_variant_rows(self):
        records = {scraper.product_key(row): row for row in self.rows}
        expected = scraper.price_rows(records)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.xlsx'
            scraper.save_excel(path, records, [], False)
            workbook = load_workbook(path, read_only=True)
            self.assertEqual(workbook.sheetnames[0], 'Prezzi')
            values = list(workbook['Prezzi'].values)
            self.assertEqual(len(values) - 1, len(expected))
            for result, original in zip(values[1:], expected):
                data = dict(zip(values[0], result))
                self.assertEqual(data['ID variante'], original['ID variante'])
                self.assertEqual(data['Prezzo buylist (€)'], original['Prezzo buylist (€)'])
                self.assertEqual(data['Credito (€)'], original['Credito (€)'])
            self.assertIn('Dati_originali', workbook.sheetnames)
            workbook.close()


if __name__ == '__main__':
    unittest.main()
