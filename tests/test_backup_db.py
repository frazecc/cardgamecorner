import gzip
import json
import tempfile
import unittest
from pathlib import Path

import backup_db


class FakeClient:
    """Finto Supabase: restituisce i blocchi come farebbe l'API, con filtro per chiave."""

    def __init__(self, cards, prices):
        self.tables = {'cgc_cards': cards, 'cgc_prices': prices}
        self.url, self.headers, self.session = 'x', {}, None


class BackupTests(unittest.TestCase):
    def run_backup(self, cards, prices):
        client = FakeClient(cards, prices)

        def fake_get(c, path, params, tries=6):
            if path == 'cgc_active':
                return [{'import_id': 'cgc-1'}]
            key = 'card_key' if path == 'cgc_cards' else 'entry_key'
            rows = [r for r in c.tables[path] if not params.get(key) or r[key] > params[key][3:]]
            return rows[:int(params['limit'])]

        original, backup_db._get = backup_db._get, fake_get
        self.addCleanup(setattr, backup_db, '_get', original)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return backup_db.run(client, directory.name), Path(directory.name)

    def test_backup_contains_every_row_even_across_pages(self):
        cards = [{'card_key': 'C%05d' % i, 'name': 'Carta %d' % i} for i in range(2500)]
        prices = [{'entry_key': 'E%05d' % i, 'card_key': 'C%05d' % (i // 2), 'buylist': 1.5} for i in range(5000)]
        manifest, out = self.run_backup(cards, prices)
        self.assertEqual(manifest['righe'], {'cards': 2500, 'prices': 5000})
        with gzip.open(out / 'cgc_backup.jsonl.gz', 'rt', encoding='utf-8') as stream:
            lines = [json.loads(line) for line in stream]
        self.assertEqual(len(lines), 7500)
        self.assertEqual(lines[0], {'t': 'cards', 'row': cards[0]})
        self.assertEqual(json.loads((out / 'manifest.json').read_text())['import_id'], 'cgc-1')

    def test_empty_database_never_produces_a_backup(self):
        with self.assertRaisesRegex(RuntimeError, 'Backup vuoto'):
            self.run_backup([], [])


if __name__ == '__main__':
    unittest.main()
