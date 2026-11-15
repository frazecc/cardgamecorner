"""Seconda copia su Google Sheets, leggendo il checkpoint locale in background.

Le scritture usano intervalli fissi: ritentare una richiesta non aggiunge doppioni.
CGC_LIVE_Prezzi contiene l'ultima osservazione di ciascuna carta; CGC_LIVE_Info
dichiara IN CORSO, PARZIALE o COMPLETO. Il checkpoint rimane la fonte di recupero.
"""

import json
import math
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sheets_sync import SheetsClient, SheetsError, MAX_CELLS, authenticated_session, column_name

LIVE_KEY = 'cgc_live'
LIVE_VALUE = 'v1'
PRICE_TITLE = 'CGC_LIVE_Prezzi'
INFO_TITLE = 'CGC_LIVE_Info'


def has_live_tag(sheet):
    return any(m.get('metadataKey') == LIVE_KEY and m.get('metadataValue') == LIVE_VALUE
               for m in sheet.get('developerMetadata', []))


def normalized(rows, width, height):
    padded = [list(row) + [''] * (width - len(row)) for row in rows]
    padded.extend([[''] * width for _ in range(height - len(padded))])
    return [[('bool', v) if isinstance(v, bool) else ('number', v) if isinstance(v, (int, float))
             else ('text', v) for v in row] for row in padded]


class LiveSheetsBackup:
    def __init__(self, checkpoint, status_path, build_rows, card_key, *, batch_size=50,
                 flush_seconds=30, client_factory=None):
        if batch_size < 1 or flush_seconds <= 0:
            raise ValueError('Dimensioni e intervallo del backup devono essere positivi.')
        self.checkpoint = Path(checkpoint)
        self.status_path = Path(status_path)
        self.build_rows = build_rows
        self.card_key = card_key
        self.batch_size = batch_size
        self.flush_seconds = flush_seconds
        self.client_factory = client_factory
        self.run_id = os.environ.get('GITHUB_RUN_ID') or secrets.token_hex(8)
        self.done = threading.Event()
        self.source_complete = False
        self.expected_cards = None
        self.expected_prices = None
        self.error = None
        self.state = 'IN CORSO'
        self.cards = 0
        self.prices = 0
        self.completed_queries = set()
        self.pending = {}
        self.positions = {}
        self.signatures = {}
        self.next_row = 2
        self.capacity = 0
        self.columns = None
        self.last_flush = time.monotonic()
        self.client = None
        self.price_id = None
        self.info_id = None
        self.other_cells = 0
        self.thread = threading.Thread(target=self._run, name='google-sheets-backup', daemon=True)

    def start(self):
        self._status()
        self.thread.start()
        return self

    def finish(self, complete, expected_cards=None, expected_prices=None):
        self.source_complete = complete
        self.expected_cards = expected_cards
        self.expected_prices = expected_prices
        self.done.set()
        last_report = time.monotonic()
        while self.thread.is_alive():
            self.thread.join(timeout=5)
            if self.thread.is_alive() and time.monotonic() - last_report >= 30:
                print(f'Google live: completo il backup, {self.cards} carte confermate.', flush=True)
                last_report = time.monotonic()
        return self.error is None and (not complete or self.state == 'COMPLETO')

    def _status(self):
        data = {'state': self.state, 'run_id': self.run_id, 'cards': self.cards,
                'price_rows': self.prices, 'queries_completed': len(self.completed_queries),
                'source_complete': self.source_complete, 'error': self.error,
                'updated_utc': datetime.now(timezone.utc).isoformat()}
        temp = self.status_path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.status_path)

    def _info(self, state=None, cards=None, prices=None):
        return [['Voce', 'Valore'], ['Stato', state or self.state], ['ID esecuzione', self.run_id],
                ['Carte confermate su Google', self.cards if cards is None else cards],
                ['Voci prezzo attive', self.prices if prices is None else prices],
                ['Ricerche concluse', len(self.completed_queries)],
                ['Fonte', 'https://www.cardgamecorner.com/it/buylist-2026?game=pokemon'],
                ['Aggiornamento UTC', datetime.now(timezone.utc).isoformat()],
                ['Nota', 'Righe vuote possono derivare da una carta aggiornata con meno varianti. Lo stato è COMPLETO solo dopo la verifica finale.']]

    def _setup(self):
        meta = self.client.metadata()
        existing = {s['properties']['title']: s for s in meta.get('sheets', [])}
        used = {s['properties']['sheetId'] for s in meta.get('sheets', [])}
        def fresh_id():
            while True:
                value = secrets.randbelow(2_000_000_000) + 1
                if value not in used:
                    used.add(value)
                    return value
        self.price_id, self.info_id = fresh_id(), fresh_id()
        requests = []
        deleted = set()
        for title in (PRICE_TITLE, INFO_TITLE):
            backup_title = title.replace('CGC_LIVE_', 'CGC_LIVE_PRECEDENTE_')
            for candidate in (title, backup_title):
                if candidate in existing and not has_live_tag(existing[candidate]):
                    raise SheetsError(f'La scheda {candidate} non appartiene al backup live: nessuna sovrascrittura.')
            if backup_title in existing:
                ident = existing[backup_title]['properties']['sheetId']
                requests.append({'deleteSheet': {'sheetId': ident}})
                deleted.add(ident)
        # Prima crea le nuove schede visibili: il documento non resta mai senza
        # schede visibili anche se le precedenti erano le sole del documento.
        for title, ident, rows, cols in [('_CGC_live_new_' + self.run_id + '_Prezzi', self.price_id, 2048, len(self.columns)),
                                       ('_CGC_live_new_' + self.run_id + '_Info', self.info_id, 20, 2)]:
            requests.extend([{'addSheet': {'properties': {'sheetId': ident, 'title': title,
                'gridProperties': {'rowCount': rows, 'columnCount': cols, 'frozenRowCount': 1}}}},
                {'createDeveloperMetadata': {'developerMetadata': {'metadataKey': LIVE_KEY,
                 'metadataValue': LIVE_VALUE, 'visibility': 'DOCUMENT', 'location': {'sheetId': ident}}}}])
        for title, ident in ((PRICE_TITLE, self.price_id), (INFO_TITLE, self.info_id)):
            old = existing.get(title)
            if old:
                requests.append({'updateSheetProperties': {'properties': {'sheetId': old['properties']['sheetId'],
                    'title': title.replace('CGC_LIVE_', 'CGC_LIVE_PRECEDENTE_'), 'hidden': True}, 'fields': 'title,hidden'}})
            requests.append({'updateSheetProperties': {'properties': {'sheetId': ident, 'title': title}, 'fields': 'title'}})
        self.other_cells = sum(s['properties'].get('gridProperties', {}).get('rowCount', 0) *
                               s['properties'].get('gridProperties', {}).get('columnCount', 0)
                               for s in meta.get('sheets', []) if s['properties']['sheetId'] not in deleted)
        if self.other_cells + 2048 * len(self.columns) + 40 > MAX_CELLS:
            raise SheetsError('Spazio celle insufficiente per il backup live; il checkpoint locale viene conservato.')
        try:
            self.client.batch(requests)
        except SheetsError:
            observed = {s['properties']['sheetId']: s['properties']['title'] for s in self.client.metadata().get('sheets', [])}
            if observed.get(self.price_id) != PRICE_TITLE or observed.get(self.info_id) != INFO_TITLE:
                raise
        self.capacity = 2048
        self._write_ranges([{'range': self.client.values(PRICE_TITLE, 1, 1, len(self.columns)), 'values': [self.columns]},
                            self._info_range()])

    def _info_range(self, state=None, cards=None, prices=None):
        values = self._info(state, cards, prices)
        return {'range': self.client.values(INFO_TITLE, 1, len(values), 2), 'values': values}

    def _write_ranges(self, ranges):
        # values:batchUpdate è idempotente per questi indirizzi fissi: anche una
        # risposta persa non causa append ripetuti. L'eco del server viene verificata.
        response = self.client.request('POST', '/values:batchUpdate', body={
            'valueInputOption': 'RAW', 'data': ranges, 'includeValuesInResponse': True,
            'responseValueRenderOption': 'UNFORMATTED_VALUE'})
        replies = response.get('responses', [])
        if len(replies) != len(ranges):
            raise SheetsError('Google live: numero di risposte diverso dai blocchi inviati.')
        for sent, received in zip(ranges, replies):
            expected = sent['values']
            actual = received.get('updatedData', {}).get('values', [])
            width = len(expected[0])
            if normalized(actual, width, len(expected)) != normalized(expected, width, len(expected)):
                raise SheetsError('Google live: verifica dei valori restituiti non riuscita.')

    def _ensure_capacity(self, needed):
        if needed <= self.capacity:
            return
        capacity = math.ceil(needed / 2048) * 2048
        if self.other_cells + capacity * len(self.columns) + 40 > MAX_CELLS:
            raise SheetsError('Google live: limite celle raggiunto; nessun dato locale viene scartato.')
        # Ridimensionare allo stesso valore può essere ritentato in sicurezza.
        self.client.request('POST', ':batchUpdate', body={'requests': [{'updateSheetProperties': {
            'properties': {'sheetId': self.price_id, 'gridProperties': {'rowCount': capacity}},
            'fields': 'gridProperties.rowCount'}}]})
        self.capacity = capacity

    def _add(self, row):
        key = self.card_key(row)
        prices = self.build_rows({key: row})
        if prices and self.columns is None:
            self.columns = list(prices[0])
        values = [[('' if p.get(c) is None else p[c]) for c in self.columns] for p in prices] if prices else []
        signature = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        previous_signature = self.pending[key][1] if key in self.pending else self.signatures.get(key)
        if previous_signature == signature:
            return
        self.pending[key] = (values, signature)
        if self.columns is not None and len(self.pending) >= self.batch_size:
            self._flush()

    def _flush(self):
        if not self.pending or self.columns is None:
            return
        if self.price_id is None:
            self._setup()
        positions = dict(self.positions)
        next_row, total = self.next_row, self.prices
        ranges = []
        width = len(self.columns)
        for key, (values, _) in self.pending.items():
            old_start, old_capacity, old_count = positions.get(key, (0, 0, 0))
            count = len(values)
            total += count - old_count
            if count <= old_capacity:
                start, allocated = old_start, old_capacity
            else:
                if old_capacity:
                    ranges.append({'range': self.client.values(PRICE_TITLE, old_start, old_start + old_capacity - 1, width),
                                   'values': [[''] * width for _ in range(old_capacity)]})
                start, allocated = next_row, count
                next_row += count
            if allocated:
                data = values + [[''] * width for _ in range(allocated - count)]
                ranges.append({'range': self.client.values(PRICE_TITLE, start, start + allocated - 1, width), 'values': data})
            positions[key] = (start, allocated, count)
        self._ensure_capacity(next_row - 1)
        # Un blocco di 50 carte può contenere molte più di 50 varianti. Suddivide
        # anche per byte/righe; la conferma del gruppo arriva soltanto alla fine.
        from sheets_sync import chunks
        packed, size = [], 0
        for item in ranges:
            start = int(item['range'].split('!A', 1)[1].split(':', 1)[0])
            # requests serializza anche caratteri Unicode come escape ASCII:
            # 300 KB UTF-8 lasciano margine al caso peggiore di espansione.
            for offset, block in chunks(item['values'], max_bytes=300_000, max_rows=1000):
                data = {'range': self.client.values(PRICE_TITLE, start + offset - 1,
                        start + offset + len(block) - 2, width), 'values': block}
                length = len(json.dumps(data, ensure_ascii=True).encode())
                if packed and size + length > 1_200_000:
                    self._write_ranges(packed)
                    packed, size = [], 0
                packed.append(data)
                size += length
        packed.append(self._info_range(cards=len(positions), prices=total))
        self._write_ranges(packed)
        self.positions, self.next_row = positions, next_row
        self.cards, self.prices = len(positions), total
        for key, (_, signature) in self.pending.items():
            self.signatures[key] = signature
        self.pending.clear()
        self.last_flush = time.monotonic()
        self._status()
        print(f'Google live: confermate {self.cards} carte / {self.prices} voci prezzo.', flush=True)

    def _consume(self):
        with self.checkpoint.open('rb') as stream:
            while True:
                position = stream.tell()
                raw = stream.readline()
                if not raw or not raw.endswith(b'\n'):
                    stream.seek(position)
                    if self.done.is_set():
                        if raw.strip():
                            raise SheetsError('Google live: riga finale del checkpoint incompleta.')
                        break
                    if self.pending and time.monotonic() - self.last_flush >= self.flush_seconds:
                        self._flush()
                    self.done.wait(0.2)
                    continue
                if not raw.strip():
                    continue
                event = json.loads(raw)
                for row in event.get('righe', []):
                    self._add(row)
                done = event.get('ricerca_completata')
                if done:
                    self.completed_queries.add(str(done.get('ID espansione')))
                if self.pending and time.monotonic() - self.last_flush >= self.flush_seconds:
                    self._flush()
        self._flush()
        if self.price_id is None:
            raise SheetsError('Google live: nessuna voce prezzo ricevuta.')
        if self.source_complete:
            if self.cards != self.expected_cards or self.prices != self.expected_prices:
                raise SheetsError('Google live: conteggi finali diversi dal risultato locale.')
            self.state = 'COMPLETO'
        else:
            self.state = 'PARZIALE'
        self._write_ranges([self._info_range()])

    def _run(self):
        session = None
        try:
            if self.client_factory:
                self.client = self.client_factory()
            else:
                from sheets_sync import DEFAULT_SPREADSHEET_ID
                session = authenticated_session()
                self.client = SheetsClient(os.environ.get('GOOGLE_SHEET_ID', '').strip() or DEFAULT_SPREADSHEET_ID, session)
            self._consume()
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, SheetsError) else type(exc).__name__
            self.state = 'ERRORE BACKUP'
            print(f'Google live: {self.error}. La raccolta prosegue nel checkpoint locale.', flush=True)
        finally:
            try:
                if session is not None:
                    session.close()
            finally:
                self._status()
