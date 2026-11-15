"""Regressioni per l'ID 1032429 ripetuto nel run 43728814267.

Il log dà l'ID, ma la vecchia diagnostica non ha salvato la riga fallita.
Le coppie sono sintetiche, costruite da una fixture reale: si verificano tutte
le possibili differenze senza assumere quale fosse quella reale.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import scraper
import sheets_sync


class DuplicateVariantTests(unittest.TestCase):
    def setUp(self):
        self.row = json.loads((Path(__file__).parent / 'fixtures/variants.json').read_text())[0]
        self.a = copy.deepcopy(scraper.parse_variants(self.row)[0])
        self.a['IdProduct'] = 1032429
        self.b = copy.deepcopy(self.a)

    def prices(self):
        self.row['product']['Variants'] = [self.a, self.b]
        return scraper.price_rows({'card': self.row})

    def test_same_id_different_cash_and_credit_keeps_both(self):
        self.b.update(BuyList=1.23, Credit=4.56)
        prices = self.prices()
        self.assertEqual(len(prices), 2)
        self.assertEqual({p['Prezzo buylist (€)'] for p in prices}, {self.a['BuyList'], 1.23})
        self.assertEqual({p['Credito (€)'] for p in prices}, {self.a['Credit'], 4.56})
        self.assertNotEqual(prices[0]['Chiave voce'], prices[1]['Chiave voce'])
        self.assertTrue(all(p['Voci con stesso ID'] == 2 for p in prices))

    def test_each_nonprice_difference_is_preserved(self):
        for field, value in [('Price', 9.87), ('Quantity', 123), ('BuyListLock', 1),
                             ('Evaluation', True), ('NoBuyList', True), ('Language', 'Japanese'),
                             ('Condition', 'HP'), ('Foil', 'Reverse Holo'), ('FirstEdition', '')]:
            with self.subTest(field=field):
                self.b = copy.deepcopy(self.a)
                self.b[field] = value
                self.assertEqual(len(self.prices()), 2)

    def test_identical_source_duplicates_are_collapsed(self):
        self.assertEqual(len(self.prices()), 1)
        self.assertFalse(self.prices()[0]['ID ripetuto nella carta'])

    def test_canonical_key_does_not_depend_on_field_order(self):
        self.b = dict(reversed(list(self.a.items())))
        self.assertEqual(len(self.prices()), 1)

    def test_latest_observation_replaces_group_but_keeps_its_duplicates(self):
        self.row['product']['Variants'] = [self.a]
        new = copy.deepcopy(self.row)
        c, d = copy.deepcopy(self.a), copy.deepcopy(self.a)
        c['BuyList'], d['BuyList'] = 12.34, 56.78
        new['product']['Variants'] = [c, d]
        merged = scraper.merge_product_row(self.row, new)
        variants = scraper.parse_variants(merged)
        self.assertEqual(len(variants), 2)
        self.assertEqual({v['BuyList'] for v in variants}, {12.34, 56.78})
        again = scraper.merge_product_row(merged, new)
        self.assertEqual(scraper.parse_variants(again), variants)

    def test_google_loader_accepts_repeated_site_id_using_entry_key(self):
        self.b.update(BuyList=1.23, Credit=4.56)
        expected = self.prices()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.xlsx'
            scraper.save_excel(path, {'card': self.row}, [], True,
                               queries_log=[{'Espansione': 'Base', 'Stato': 'COMPLETATA'}])
            tables = sheets_sync.load_tables(path)
            headers = tables['Prezzi'][0]
            actual = [dict(zip(headers, row)) for row in tables['Prezzi'][1:]]
            self.assertEqual(len(actual), 2)
            self.assertEqual([r['Chiave voce'] for r in actual], [r['Chiave voce'] for r in expected])
            self.assertEqual([r['Prezzo buylist (€)'] for r in actual], [r['Prezzo buylist (€)'] for r in expected])


if __name__ == '__main__':
    unittest.main()
