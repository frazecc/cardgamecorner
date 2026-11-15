import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

import requests
import scraper
import sheets_sync as sync


class Response:
    def __init__(self, data=None, status=200):
        self.data = data or {}
        self.status_code = status

    def json(self):
        return self.data


class MemorySheets:
    """Modello API per testare il protocollo; nessuna connessione a Google."""
    def __init__(self):
        self.sheets = {1: {'properties': {'sheetId': 1, 'title': 'Manuale',
                           'gridProperties': {'rowCount': 100, 'columnCount': 26}},
                           'developerMetadata': [], 'values': [['I miei dati']]}}
        self.calls = []
        self.fail_final = False
        self.lose_final_response = False
        self.corrupt_read = False
        self.rate_limit_once = False
        self.rate_limited = False

    def request(self, method, url, json=None, params=None, timeout=None):
        self.calls.append((method, url, copy.deepcopy(json), params))
        if '/values/' in url:
            address = unquote(url.split('/values/', 1)[1])
            match = re.fullmatch(r"'([^']+)'!A(?:(\d+):[A-Z]+(\d+)|:B)", address)
            if not match:
                raise AssertionError(address)
            title, first, last = match.groups()
            sheet = next((s for s in self.sheets.values() if s['properties']['title'] == title), None)
            if sheet is None:
                return Response(status=404)
            rows = sheet['values']
            start = int(first or 1) - 1
            end = int(last) if last else len(rows)
            if method == 'PUT':
                self.assert_raw(params)
                if self.rate_limit_once and not self.rate_limited:
                    self.rate_limited = True
                    return Response(status=429)
                while len(rows) < end:
                    rows.append([])
                rows[start:end] = copy.deepcopy(json['values'])
                return Response({'updatedRows': len(json['values'])})
            values = copy.deepcopy(rows[start:end])
            if self.corrupt_read and title.endswith('_Prezzi') and len(values) > 1:
                values[1][0] = 'dato corrotto'
            # Simula omissione API delle celle finali vuote.
            for row in values:
                while row and row[-1] == '':
                    row.pop()
            while values and not values[-1]:
                values.pop()
            return Response({'values': values})
        if method == 'GET':
            return Response({'spreadsheetId': 'test_spreadsheet_123', 'sheets': [
                {k: copy.deepcopy(v) for k, v in s.items() if k != 'values'} for s in self.sheets.values()]})
        if not url.endswith(':batchUpdate'):
            raise AssertionError(url)
        operations = json['requests']
        final = any('copyPaste' in op for op in operations)
        if final and self.fail_final:
            return Response(status=400)
        pending = copy.deepcopy(self.sheets)
        for op in operations:
            if 'addSheet' in op:
                p = copy.deepcopy(op['addSheet']['properties'])
                assert p['sheetId'] not in pending
                pending[p['sheetId']] = {'properties': p, 'developerMetadata': [], 'values': []}
            elif 'createDeveloperMetadata' in op:
                metadata = copy.deepcopy(op['createDeveloperMetadata']['developerMetadata'])
                pending[metadata['location']['sheetId']]['developerMetadata'].append(metadata)
            elif 'deleteSheet' in op:
                del pending[op['deleteSheet']['sheetId']]
            elif 'updateSheetProperties' in op:
                p = op['updateSheetProperties']['properties']
                pending[p['sheetId']]['properties']['gridProperties'].update(p.get('gridProperties', {}))
            elif 'updateCells' in op:
                pending[op['updateCells']['range']['sheetId']]['values'] = []
            elif 'copyPaste' in op:
                data = op['copyPaste']
                source = pending[data['source']['sheetId']]['values']
                pending[data['destination']['sheetId']]['values'] = copy.deepcopy(source)
            elif not ('repeatCell' in op or 'setBasicFilter' in op or 'clearBasicFilter' in op):
                raise AssertionError(op)
        self.sheets = pending
        if final and self.lose_final_response:
            raise requests.exceptions.Timeout('risposta persa')
        return Response({'replies': [{} for _ in operations]})

    def assert_raw(self, params):
        assert params['valueInputOption'] == 'RAW'


def sample_tables():
    return {
        'Prezzi': [['ID carta', 'ID variante', 'Carta', 'Prezzo buylist (€)', 'Credito (€)', 'Reverse', 'Note'],
                   ['Id:1', '001', '=Nome carta', 12.45, 14.5, False, ''],
                   ['Id:1', '002', 'Carta IT HP', 0, 0, True, '']],
        'Carte': [['ID', 'Carta'], ['Id:1', '=Nome carta']],
        'Info': [['Voce', 'Valore'], ['Stato', 'COMPLETO: verificato']],
        'Espansioni': [['Espansione', 'Stato'], ['Base', 'COMPLETATA']],
    }


class GoogleSheetsTests(unittest.TestCase):
    def client(self, memory):
        return sync.SheetsClient('test_spreadsheet_123', memory, interval=0)

    def test_atomic_publish_preserves_unrelated_sheet(self):
        memory = MemorySheets()
        original = copy.deepcopy(memory.sheets[1])
        self.client(memory).publish(sample_tables())
        self.assertEqual(memory.sheets[1], original)
        self.assertEqual(len(memory.sheets), 5)
        prices = next(s for s in memory.sheets.values() if s['properties']['title'] == 'CGC_Prezzi')
        self.assertEqual(prices['values'], sample_tables()['Prezzi'])
        self.assertTrue(sync.managed(prices))
        self.assertFalse(any(s['properties']['title'].startswith('_CGC_tmp_') for s in memory.sheets.values()))

    def test_repeat_import_updates_same_ids_and_removes_old_rows(self):
        memory = MemorySheets()
        client = self.client(memory)
        client.publish(sample_tables())
        ids = {s['properties']['title']: s['properties']['sheetId'] for s in memory.sheets.values()}
        tables = sample_tables()
        tables['Prezzi'].pop()
        tables['Prezzi'][1][3] = 99.5
        client.publish(tables)
        self.assertEqual(ids, {s['properties']['title']: s['properties']['sheetId'] for s in memory.sheets.values()})
        prices = memory.sheets[ids['CGC_Prezzi']]['values']
        self.assertEqual(prices, tables['Prezzi'])

    def test_no_overwrite_of_existing_unmanaged_tab(self):
        memory = MemorySheets()
        memory.sheets[1]['properties']['title'] = 'CGC_Prezzi'
        original = copy.deepcopy(memory.sheets)
        with self.assertRaisesRegex(sync.SheetsError, 'non appartiene'):
            self.client(memory).publish(sample_tables())
        self.assertEqual(memory.sheets, original)

    def test_failed_verification_retains_previous_complete_data(self):
        memory = MemorySheets()
        client = self.client(memory)
        client.publish(sample_tables())
        previous = copy.deepcopy(memory.sheets)
        memory.corrupt_read = True
        with self.assertRaisesRegex(sync.SheetsError, 'Verifica dati'):
            client.publish(sample_tables())
        self.assertEqual(memory.sheets, previous)

    def test_failed_final_batch_retains_previous_complete_data(self):
        memory = MemorySheets()
        client = self.client(memory)
        client.publish(sample_tables())
        previous = copy.deepcopy(memory.sheets)
        memory.fail_final = True
        with self.assertRaisesRegex(sync.SheetsError, 'HTTP 400'):
            client.publish(sample_tables())
        self.assertEqual(memory.sheets, previous)

    def test_lost_final_response_is_resolved_without_repeating_copy(self):
        memory = MemorySheets()
        memory.lose_final_response = True
        self.client(memory).publish(sample_tables())
        finals = [call for call in memory.calls if call[2] and any('copyPaste' in r for r in call[2].get('requests', []))]
        self.assertEqual(len(finals), 1)
        self.assertEqual(len(memory.sheets), 5)

    def test_rate_limit_retries_same_range_without_duplicate_rows(self):
        memory = MemorySheets()
        memory.rate_limit_once = True
        with patch.object(sync.time, 'sleep'):
            self.client(memory).publish(sample_tables())
        prices = next(s for s in memory.sheets.values() if s['properties']['title'] == 'CGC_Prezzi')
        self.assertEqual(prices['values'], sample_tables()['Prezzi'])
        puts = [c for c in memory.calls if c[0] == 'PUT']
        self.assertEqual(puts[0][1:3], puts[1][1:3])

    def test_capacity_failure_changes_nothing(self):
        memory = MemorySheets()
        original = copy.deepcopy(memory.sheets)
        with patch.object(sync, 'MAX_CELLS', 1):
            with self.assertRaisesRegex(sync.SheetsError, 'Spazio celle'):
                self.client(memory).publish(sample_tables())
        self.assertEqual(memory.sheets, original)

    def test_partial_publish_uses_separate_tabs_and_keeps_complete_data(self):
        memory = MemorySheets()
        client = self.client(memory)
        client.publish(sample_tables())
        previous = copy.deepcopy(memory.sheets)
        partial = sample_tables()
        partial['Info'][1][1] = 'PARZIALE'
        client.publish(partial, prefix='CGC_PARZIALE_')
        for key, value in previous.items():
            self.assertEqual(memory.sheets[key], value)
        self.assertTrue(any(s['properties']['title'] == 'CGC_PARZIALE_Prezzi' for s in memory.sheets.values()))

    def test_partial_cannot_replace_complete_tabs(self):
        memory = MemorySheets()
        original = copy.deepcopy(memory.sheets)
        partial = sample_tables()
        partial['Info'][1][1] = 'PARZIALE'
        with self.assertRaisesRegex(sync.SheetsError, 'CGC_PARZIALE_'):
            self.client(memory).publish(partial)
        self.assertEqual(memory.sheets, original)

    def test_utf8_chunks_respect_byte_limit_and_preserve_order(self):
        rows = [['€日本語' * 5, str(i)] for i in range(10)]
        blocks = list(sync.chunks(rows, max_bytes=200, max_rows=3))
        self.assertEqual([r for _, b in blocks for r in b], rows)
        for start, block in blocks:
            self.assertEqual(block[0][1], str(start - 1))
            body = json.dumps({'values': block}, ensure_ascii=False, separators=(',', ':')).encode()
            self.assertLessEqual(len(body), 200)

    def test_spreadsheet_id_and_raw_text(self):
        self.assertEqual(sync.spreadsheet_id('https://docs.google.com/spreadsheets/d/ABC123456789/edit#gid=1'), 'ABC123456789')
        self.assertEqual(sync.cell_value("'=SUM(A1:A5)"), '=SUM(A1:A5)')
        self.assertIs(sync.cell_value(False), False)
        with self.assertRaises(sync.SheetsError):
            sync.spreadsheet_id('https://example.com/malicious')
        with self.assertRaises(sync.SheetsError):
            sync.cell_value('x' * 50000)

    def test_loads_complete_export_and_refuses_partial(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/variants.json').read_text())
        records = {scraper.product_key(row): row for row in rows}
        queries = [{'Espansione': 'Base', 'Stato': 'COMPLETATA'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'complete.xlsx'
            scraper.save_excel(path, records, [], True, queries_log=queries)
            tables = sync.load_tables(path)
            self.assertEqual(len(tables['Prezzi']) - 1, len(scraper.price_rows(records)))
            self.assertEqual(set(tables), set(sync.TABLES))
            scraper.save_excel(path, records, [], False, queries_log=queries)
            with self.assertRaisesRegex(sync.SheetsError, 'COMPLETA'):
                sync.load_tables(path)
            partial_tables = sync.load_tables(path, allow_partial=True)
            self.assertEqual(len(partial_tables['Prezzi']) - 1, len(scraper.price_rows(records)))


if __name__ == '__main__':
    unittest.main()
