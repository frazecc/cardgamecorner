import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import esporta_json

ROOT = Path(__file__).resolve().parent.parent


def row(n, status='PREZZO DISPONIBILE', language='Italiano', buylist='1.5', credit='3', card='C1'):
    return {'entry_key': f'E{n:05d}', 'card_key': card, 'name': 'Abra', 'name_en': None, 'expansion': 'Base',
            'serial_number': '1', 'image': '', 'language': language, 'condition': 'NM/M', 'foil': None,
            'first_edition': None, 'buylist': buylist, 'credit': credit, 'status': status}


class EsportaJsonTests(unittest.TestCase):
    def test_variants_are_grouped_by_card_and_status(self):
        rows = [row(1), row(2, language='English'), row(3, card='C2', status='VALUTAZIONE SU RICHIESTA', buylist=None, credit=None)]
        groups = esporta_json.build_groups(rows)
        self.assertEqual(sorted(groups), ['escluse', 'prezzi', 'richiesta', 'senza-prezzo'])
        self.assertEqual(len(groups['prezzi']['carte']), 1)
        self.assertEqual(len(groups['prezzi']['carte'][0]['v']), 2)
        self.assertEqual(groups['richiesta']['carte'][0]['v'][0][4:6], [None, None])
        self.assertEqual(groups['escluse']['carte'], [])

    def test_numbers_and_empty_fields(self):
        variant = esporta_json.build_groups([row(1, buylist='1,50', credit='3.00')])['prezzi']['carte'][0]['v'][0]
        self.assertEqual(variant, ['Italiano', 'NM/M', '', '', 1.5, 3, 'PREZZO DISPONIBILE'])

    def test_unknown_status_stops_everything(self):
        with self.assertRaises(ValueError):
            esporta_json.build_groups([row(1, status='STRANO')])

    def test_site_files_and_meta(self):
        rows = [row(1), row(2, language='English')]
        groups = esporta_json.build_groups(rows)
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
            meta = esporta_json.write_site(groups, rows, {'dal': '2026-11-01T00:00:00Z', 'fino_al': '2026-11-26T11:00:00Z'},
                                           ROOT / 'docs', tmp, now=now)
            for name in ('meta', 'prezzi', 'richiesta', 'escluse', 'senza-prezzo'):
                self.assertTrue(Path(tmp, 'data', name + '.json').is_file(), name)
            self.assertTrue(Path(tmp, 'index.html').is_file())
            saved = json.loads(Path(tmp, 'data', 'meta.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, meta)
            self.assertEqual(saved['generato_il'], '2026-11-26T12:00:00Z')
            self.assertEqual(saved['conteggi']['righe'], 2)
            self.assertEqual(saved['valori_filtri']['prezzi']['language'], [['English', 1], ['Italiano', 1]])
            self.assertEqual(saved['dati_controllati_dal'], '2026-11-01T00:00:00Z')

    def test_expansion_list_includes_empty_searches(self):
        rows = [row(1), row(2, card='C2')]
        groups = esporta_json.build_groups(rows)
        with tempfile.TemporaryDirectory() as tmp:
            meta = esporta_json.write_site(groups, rows, None, ROOT / 'docs', tmp, searches=['Base', 'Vuota', ''])
        self.assertEqual(meta['espansioni'], [{'n': 'Base', 'c': {'prezzi': 2}}, {'n': 'Vuota', 'c': {}}])


if __name__ == '__main__':
    unittest.main()
