import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

import requests
from live_sheets import LiveSheetsBackup, PRICE_TITLE, INFO_TITLE
from sheets_sync import SheetsClient
from test_sheets import MemorySheets, Response


def card(ident, price=1.25, variants=2):
    return {'product': {'Id': str(ident)}, 'name': '=Carta ' + str(ident), 'price': price, 'variants': variants}


def key(row):
    return 'Id:' + row['product']['Id']


def prices(records):
    return [{'ID carta': ident, 'ID variante': str(i), 'Carta': row['name'],
             'Prezzo': row['price'] + i, 'Credito': row['price'] + i + 1, 'Valutazione': False}
            for ident, row in records.items() for i in range(row['variants'])]


class LiveMemory(MemorySheets):
    def __init__(self):
        super().__init__()
        self.value_batches = []
        self.data_seen = threading.Event()
        self.lose_value_response_once = False
        self.fail_value_call = None

    def close(self):
        pass

    def request(self, method, url, json=None, params=None, timeout=None):
        if not url.endswith('/values:batchUpdate'):
            return super().request(method, url, json, params, timeout)
        self.value_batches.append(copy.deepcopy(json))
        if self.fail_value_call == len(self.value_batches):
            return Response(status=403)
        assert json['valueInputOption'] == 'RAW' and json['includeValuesInResponse']
        previous = copy.deepcopy(self.sheets)
        replies = []
        for data in json['data']:
            address = url.split('/values:', 1)[0] + '/values/' + quote(data['range'], safe='')
            response = super().request('PUT', address, {'values': data['values']}, {'valueInputOption': 'RAW'}, timeout)
            if response.status_code != 200:
                self.sheets = previous
                return response
            response = super().request('GET', address, None, {'valueRenderOption': 'UNFORMATTED_VALUE'}, timeout)
            replies.append({'updatedData': {'values': response.json().get('values', [])}})
        if any(PRICE_TITLE in data['range'] and "!A1:" not in data['range'] for data in json['data']):
            self.data_seen.set()
        if self.lose_value_response_once:
            self.lose_value_response_once = False
            raise requests.exceptions.Timeout('risposta persa dopo il salvataggio')
        return Response({'responses': replies})


class LiveBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def start(self, rows, *, memory=None, batch_size=50, flush_seconds=30):
        memory = memory or LiveMemory()
        checkpoint = self.root / 'checkpoint.jsonl'
        checkpoint.write_text(''.join(json.dumps({'righe': [row]}) + '\n' for row in rows))
        status = self.root / 'live.status.json'
        backup = LiveSheetsBackup(checkpoint, status, prices, key, batch_size=batch_size,
                                  flush_seconds=flush_seconds,
                                  client_factory=lambda: SheetsClient('test_spreadsheet_123', memory, interval=0)).start()
        return backup, memory, checkpoint, status

    def visible_rows(self, memory):
        sheet = next(s for s in memory.sheets.values() if s['properties']['title'] == PRICE_TITLE)
        return [row for row in sheet['values'][1:] if row and row[0]]

    def test_50_cards_per_batch_and_last_incomplete_batch(self):
        rows = [card(i) for i in range(120)]
        backup, memory, _, status = self.start(rows)
        self.assertTrue(backup.finish(True, 120, 240))
        data_batches = [b for b in memory.value_batches if any(PRICE_TITLE in d['range'] and '!A1:' not in d['range'] for d in b['data'])]
        counts = [len({r[0] for d in b['data'] if PRICE_TITLE in d['range'] for r in d['values'] if r and r[0]}) for b in data_batches]
        self.assertEqual(counts, [50, 50, 20])
        self.assertEqual(len(self.visible_rows(memory)), 240)
        self.assertEqual(json.loads(status.read_text())['state'], 'COMPLETO')

    def test_small_batch_is_saved_while_collection_is_still_running(self):
        backup, memory, checkpoint, _ = self.start([card(i) for i in range(5)], flush_seconds=0.01)
        self.assertTrue(memory.data_seen.wait(timeout=3))
        self.assertFalse(backup.done.is_set())
        with checkpoint.open('a') as stream:
            for i in range(5, 10):
                stream.write(json.dumps({'righe': [card(i)]}) + '\n')
        self.assertTrue(backup.finish(True, 10, 20))
        self.assertEqual(len(self.visible_rows(memory)), 20)

    def test_identical_repeated_cards_do_not_add_duplicate_rows(self):
        backup, memory, _, _ = self.start([card(1)] * 100)
        self.assertTrue(backup.finish(True, 1, 2))
        self.assertEqual(len(self.visible_rows(memory)), 2)

    def test_updated_card_clears_obsolete_variants(self):
        backup, memory, _, _ = self.start([card(1, variants=3), card(1, price=10, variants=1)], batch_size=1)
        self.assertTrue(backup.finish(True, 1, 1))
        rows = self.visible_rows(memory)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], 10)

    def test_increasing_variants_moves_range_without_leaving_old_prices(self):
        backup, memory, _, _ = self.start([card(1, variants=1), card(1, price=10, variants=3)], batch_size=1)
        self.assertTrue(backup.finish(True, 1, 3))
        self.assertEqual([row[3] for row in self.visible_rows(memory)], [10, 11, 12])

    def test_pending_update_reverting_to_original_price_is_not_lost(self):
        rows = [card(1, 1), card(2, 2), card(1, 10), card(1, 1)]
        backup, memory, _, _ = self.start(rows, batch_size=2)
        self.assertTrue(backup.finish(True, 2, 4))
        current = [row for row in self.visible_rows(memory) if row[0] == 'Id:1']
        self.assertEqual([row[3] for row in current], [1, 2])

    def test_lost_response_retries_same_ranges_without_duplicates(self):
        memory = LiveMemory()
        memory.lose_value_response_once = True
        from unittest.mock import patch
        with patch('sheets_sync.time.sleep'):
            backup, memory, _, _ = self.start([card(i) for i in range(3)], memory=memory)
            self.assertTrue(backup.finish(True, 3, 6))
        self.assertEqual(len(self.visible_rows(memory)), 6)
        self.assertEqual(memory.value_batches[0], memory.value_batches[1])

    def test_google_failure_keeps_checkpoint_and_does_not_claim_completion(self):
        memory = LiveMemory()
        memory.fail_value_call = 3
        rows = [card(i) for i in range(100)]
        backup, memory, checkpoint, status = self.start(rows, memory=memory)
        self.assertFalse(backup.finish(True, 100, 200))
        self.assertEqual(len(checkpoint.read_text().splitlines()), 100)
        self.assertEqual(json.loads(status.read_text())['state'], 'ERRORE BACKUP')
        self.assertEqual(len(self.visible_rows(memory)), 100)  # Primo gruppo di 50 carte.

    def test_partial_collection_is_never_marked_complete(self):
        backup, memory, _, status = self.start([card(1)])
        self.assertTrue(backup.finish(False))
        self.assertEqual(json.loads(status.read_text())['state'], 'PARZIALE')

    def test_final_count_mismatch_is_reported(self):
        backup, _, _, status = self.start([card(1)])
        self.assertFalse(backup.finish(True, 999, 2))
        self.assertEqual(json.loads(status.read_text())['state'], 'ERRORE BACKUP')

    def test_previous_live_is_retained_and_unrelated_data_stays_intact(self):
        memory = LiveMemory()
        original = copy.deepcopy(memory.sheets[1])
        first, _, _, _ = self.start([card(1)], memory=memory)
        self.assertTrue(first.finish(True, 1, 2))
        second, _, _, _ = self.start([card(2)], memory=memory)
        self.assertTrue(second.finish(True, 1, 2))
        backup = next(s for s in memory.sheets.values() if s['properties']['title'] == 'CGC_LIVE_PRECEDENTE_Prezzi')
        self.assertEqual(backup['values'][1][0], 'Id:1')
        self.assertEqual(self.visible_rows(memory)[0][0], 'Id:2')
        self.assertEqual(memory.sheets[1], original)

    def test_large_card_and_grid_growth_keep_all_variants(self):
        backup, memory, _, _ = self.start([card(1, variants=2500)])
        self.assertTrue(backup.finish(True, 1, 2500))
        self.assertEqual(len(self.visible_rows(memory)), 2500)

    def test_cards_without_variants_are_counted_without_fake_price_rows(self):
        backup, memory, _, _ = self.start([card(0, variants=0), card(1, variants=1)])
        self.assertTrue(backup.finish(True, 2, 1))
        self.assertEqual(len(self.visible_rows(memory)), 1)


if __name__ == '__main__':
    unittest.main()
