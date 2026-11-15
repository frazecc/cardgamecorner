"""Importa un Excel COMPLETO dello scraper in un Google Sheet esistente.

pip install 'google-auth[requests]>=2.38,<3' openpyxl
Destinazione preconfigurata: IMPORT CGC. GOOGLE_SHEET_ID può cambiarla.
GOOGLE_SERVICE_ACCOUNT_JSON: JSON del service account, salvato nei Secrets GitHub.
In alternativa usa GOOGLE_APPLICATION_CREDENTIALS per un file locale o ADC.
Non serve Google Drive API: basta Sheets API e condividere il documento come Editor.
"""

import argparse
import json
import math
import os
import random
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook

SOURCE_URL = 'https://www.cardgamecorner.com/it/buylist-2026?game=pokemon'
DEFAULT_SPREADSHEET_ID = '1SH8_HmCd5YV37woGIZUwemy_GLh6Y9Z_28OQf_B_Rlw'
SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
TABLES = ('Prezzi', 'Carte', 'Info', 'Espansioni')
OWNER_KEY = 'cgc_export'
OWNER_VALUE = 'cardgamecorner-buylist-v1'
MAX_CELLS = 20_000_000
MAX_PAYLOAD = 1_500_000


class SheetsError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def spreadsheet_id(value):
    value = value.strip()
    if value.startswith('https://docs.google.com/spreadsheets/d/'):
        value = value.split('/d/', 1)[1].split('/', 1)[0].split('?', 1)[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,}', value):
        raise SheetsError('GOOGLE_SHEET_ID deve contenere un ID o un URL valido di Google Sheets.')
    return value


def cell_value(value):
    if value is None:
        return ''
    # Rimuove la protezione aggiunta dall'export Excel. Sheets usa RAW, quindi
    # nomi che iniziano con = restano testo e non diventano formule.
    if isinstance(value, str) and value.startswith(("'=", "'+", "'-", "'@")):
        value = value[1:]
    if not isinstance(value, (str, bool, int, float)):
        raise SheetsError('Tipo di cella non supportato.')
    if isinstance(value, float) and not math.isfinite(value):
        raise SheetsError('Numero non finito nell’Excel.')
    if isinstance(value, str) and len(value) > 49_000:
        raise SheetsError('Cella troppo lunga per Google Sheets: nessun troncamento automatico.')
    return value


def load_tables(path, allow_partial=False):
    """Legge solo i fogli utili a Sheets, conserva tutte le righe Prezzi."""
    with_workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if any(name not in with_workbook.sheetnames for name in TABLES):
            raise SheetsError('Excel privo dei fogli necessari: serve lo scraper 4.0 o successivo.')
        tables = {}
        for name in TABLES:
            rows = []
            for row in with_workbook[name].iter_rows():
                if any(cell.data_type == 'f' for cell in row):
                    raise SheetsError(f'Formula inattesa nel foglio {name}.')
                rows.append([cell_value(cell.value) for cell in row])
            while rows and not any(value != '' for value in rows[-1]):
                rows.pop()
            if not rows or not any(rows[0]):
                raise SheetsError(f'Foglio {name} senza intestazioni.')
            tables[name] = rows
        info = {row[0]: row[1] for row in tables['Info'][1:] if len(row) >= 2}
        complete = str(info.get('Stato', '')).startswith('COMPLETO:')
        partial = info.get('Stato') == 'PARZIALE'
        if (not complete and not (allow_partial and partial)) or info.get('Fonte') != SOURCE_URL:
            raise SheetsError('Importazione rifiutata: il file non è una buylist COMPLETA dello scraper.')
        if int(info.get('Varianti nel foglio Prezzi', -1)) != len(tables['Prezzi']) - 1:
            raise SheetsError('Conteggio varianti diverso dalle righe Prezzi.')
        if int(info.get('Carte distinte', -1)) != len(tables['Carte']) - 1:
            raise SheetsError('Conteggio carte diverso dalle righe Carte.')
        status_index = tables['Espansioni'][0].index('Stato')
        query_rows = tables['Espansioni'][1:]
        if int(info.get('Ricerche completate', -1)) != sum(row[status_index] == 'COMPLETATA' for row in query_rows):
            raise SheetsError('Conteggio ricerche completate incoerente nell’Excel.')
        if complete and any(row[status_index] != 'COMPLETATA' for row in query_rows):
            raise SheetsError('Non tutte le ricerche risultano completate nell’Excel.')
        seen = set()
        headers = tables['Prezzi'][0]
        a, b = headers.index('ID carta'), headers.index('ID variante')
        entry_index = headers.index('Chiave voce') if 'Chiave voce' in headers else None
        for row in tables['Prezzi'][1:]:
            key = (row[a], row[entry_index]) if entry_index is not None else (row[a], row[b])
            if not row[a] or not row[b] or not all(key) or key in seen:
                raise SheetsError('Chiave voce mancante o duplicata nel foglio Prezzi.')
            seen.add(key)
        return tables
    finally:
        with_workbook.close()


def chunks(rows, max_bytes=MAX_PAYLOAD, max_rows=2500):
    """Blocchi limitati per byte UTF-8 e righe, compresi caratteri non ASCII."""
    block, size, start = [], 40, 1
    for row in rows:
        length = len(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')) + 1
        if length + 40 > max_bytes:
            raise SheetsError('Una riga supera la dimensione massima della richiesta.')
        if block and (size + length > max_bytes or len(block) >= max_rows):
            yield start, block
            start += len(block)
            block, size = [], 40
        block.append(row)
        size += length
    if block:
        yield start, block


def column_name(count):
    result = ''
    while count:
        count, remainder = divmod(count - 1, 26)
        result = chr(65 + remainder) + result
    return result


def managed(sheet):
    return any(m.get('metadataKey') == OWNER_KEY and m.get('metadataValue') == OWNER_VALUE
               for m in sheet.get('developerMetadata', []))


class SheetsClient:
    def __init__(self, sheet_id, session, interval=1.1):
        self.sheet_id = spreadsheet_id(sheet_id)
        self.session = session
        self.interval = interval
        self.last_request = None
        self.base = f'https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}'

    def request(self, method, suffix='', *, body=None, params=None, retry=True):
        import requests
        for attempt in range(6 if retry else 1):
            if self.last_request is not None:
                time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.request(method, self.base + suffix, json=body,
                                                params=params, timeout=(15, 180))
            except requests.exceptions.RequestException as exc:
                if not retry or attempt == 5:
                    raise SheetsError('Connessione Google Sheets non riuscita o risposta non confermata.', retryable=True) from exc
            else:
                if 200 <= response.status_code < 300:
                    return response.json()
                if not retry or response.status_code not in (429, 500, 502, 503, 504) or attempt == 5:
                    hints = {403: 'Abilita Sheets API e condividi il documento con il service account come Editor.',
                             404: 'Verifica ID del documento e accesso del service account.'}
                    raise SheetsError(f'Google Sheets HTTP {response.status_code}. ' + hints.get(response.status_code, 'Importazione interrotta.'),
                                      retryable=response.status_code in (429, 500, 502, 503, 504))
            time.sleep(min(32, 2 ** attempt) + random.random())
        raise SheetsError('Tentativi Google Sheets esauriti.')

    def metadata(self):
        return self.request('GET', params={'fields': 'spreadsheetId,sheets(properties,developerMetadata)'})

    def batch(self, requests):
        # addSheet/copyPaste/deleteSheet non vengono ripetuti ciecamente dopo
        # timeout: potrebbero essere già stati applicati dal server.
        return self.request('POST', ':batchUpdate', body={'requests': requests}, retry=False)

    def values(self, title, first, last, columns):
        return "'" + title.replace("'", "''") + f"'!A{first}:{column_name(columns)}{last}"

    def write_rows(self, title, rows):
        width = len(rows[0])
        for start, block in chunks(rows):
            address = self.values(title, start, start + len(block) - 1, width)
            self.request('PUT', '/values/' + quote(address, safe=''),
                         params={'valueInputOption': 'RAW'}, body={'values': block, 'majorDimension': 'ROWS'})
        # Rilegge tutto, compresi prezzi numerici, ID stringa e righe a zero.
        for start, block in chunks(rows):
            address = self.values(title, start, start + len(block) - 1, width)
            result = self.request('GET', '/values/' + quote(address, safe=''),
                                  params={'valueRenderOption': 'UNFORMATTED_VALUE'})
            actual = result.get('values', [])
            padded = [list(row) + [''] * (width - len(row)) for row in actual]
            padded.extend([[''] * width for _ in range(len(block) - len(padded))])
            # bool deve restare bool, mentre Google può restituire 1.0 come 1.
            def normalized(data):
                return [[('bool', v) if isinstance(v, bool) else
                         ('number', v) if isinstance(v, (int, float)) else ('text', v) for v in row] for row in data]
            if normalized(padded) != normalized(block):
                raise SheetsError(f'Verifica dati fallita per {title}, riga {start}.')

    def published(self, run_id, prefix='CGC_'):
        address = "'" + prefix + "Info'!A:B"
        result = self.request('GET', '/values/' + quote(address, safe=''),
                              params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        return any(row == ['ID importazione', run_id] for row in result.get('values', []))

    def publish(self, original_tables, prefix='CGC_'):
        if prefix not in ('CGC_', 'CGC_PARZIALE_'):
            raise SheetsError('Prefisso di destinazione non autorizzato.')
        source_info = {row[0]: row[1] for row in original_tables['Info'][1:] if len(row) >= 2}
        is_partial = source_info.get('Stato') == 'PARZIALE'
        if is_partial != (prefix == 'CGC_PARZIALE_'):
            raise SheetsError('Un risultato parziale deve usare esclusivamente le schede CGC_PARZIALE_.')
        run_id = secrets.token_hex(8)
        tables = {name: [list(row) for row in original_tables[name]] for name in TABLES}
        tables['Info'].extend([['ID importazione', run_id],
                               ['Importazione Google UTC', datetime.now(timezone.utc).isoformat()]])
        metadata = self.metadata()
        sheets = metadata.get('sheets', [])
        existing = {sheet['properties']['title']: sheet for sheet in sheets}
        ids = {sheet['properties']['sheetId'] for sheet in sheets}

        def new_id():
            while True:
                value = secrets.randbelow(2_000_000_000) + 1
                if value not in ids:
                    ids.add(value)
                    return value

        def dimensions(rows):
            return {'rowCount': max(2, len(rows)), 'columnCount': len(rows[0])}

        def tag(sheet_id, key=OWNER_KEY, value=OWNER_VALUE):
            return {'createDeveloperMetadata': {'developerMetadata': {
                'metadataKey': key, 'metadataValue': value, 'visibility': 'DOCUMENT',
                'location': {'sheetId': sheet_id}}}}

        stages, targets = {}, {}
        current_cells = sum(s['properties'].get('gridProperties', {}).get('rowCount', 0) *
                            s['properties'].get('gridProperties', {}).get('columnCount', 0) for s in sheets)
        stage_cells, target_growth = 0, 0
        for name, rows in tables.items():
            title = prefix + name
            old = existing.get(title)
            if old and not managed(old):
                raise SheetsError(f'La scheda {title} esiste ma non appartiene a questo importatore. Rinominala prima di procedere.')
            target_id = old['properties']['sheetId'] if old else new_id()
            targets[name] = (title, target_id, old)
            stages[name] = ('_CGC_tmp_' + run_id + '_' + name, new_id())
            grid = dimensions(rows)
            cells = grid['rowCount'] * grid['columnCount']
            stage_cells += cells
            old_grid = old['properties'].get('gridProperties', {}) if old else {}
            old_cells = old_grid.get('rowCount', 0) * old_grid.get('columnCount', 0)
            target_growth += max(0, cells - old_cells)
        if current_cells + stage_cells + target_growth > MAX_CELLS:
            raise SheetsError('Spazio celle insufficiente per caricare e verificare tutti i dati. Nessuna riga sarà troncata.')

        create = []
        for name, (title, sheet_id) in stages.items():
            create.extend([{'addSheet': {'properties': {'sheetId': sheet_id, 'title': title,
                            'hidden': True, 'gridProperties': dimensions(tables[name])}}},
                           tag(sheet_id, 'cgc_stage', run_id)])
        try:
            try:
                self.batch(create)
            except SheetsError:
                # Una risposta persa dopo addSheet non deve creare doppioni.
                observed = {s['properties']['sheetId'] for s in self.metadata().get('sheets', [])}
                if not all(sheet_id in observed for _, sheet_id in stages.values()):
                    raise
            for name, (title, _) in stages.items():
                print(f'Google Sheets: carico e verifico {name}, {len(tables[name]) - 1} righe.', flush=True)
                self.write_rows(title, tables[name])

            publish = []
            for name, (title, target_id, old) in targets.items():
                grid = dimensions(tables[name])
                if old:
                    publish.append({'clearBasicFilter': {'sheetId': target_id}})
                    publish.append({'updateSheetProperties': {'properties': {'sheetId': target_id,
                                    'gridProperties': grid}, 'fields': 'gridProperties.rowCount,gridProperties.columnCount'}})
                else:
                    publish.extend([{'addSheet': {'properties': {'sheetId': target_id, 'title': title,
                                     'gridProperties': grid}}}, tag(target_id)])
                target_range = {'sheetId': target_id, 'startRowIndex': 0, 'endRowIndex': grid['rowCount'],
                                'startColumnIndex': 0, 'endColumnIndex': grid['columnCount']}
                source_range = dict(target_range, sheetId=stages[name][1])
                # Cancella eventuali vecchie celle e copia nel MEDESIMO sheetId:
                # link gid e formule in altre schede continuano a puntare qui.
                publish.extend([
                    # Il rettangolo viene sovrascritto interamente (anche celle
                    # vuote); evitare una cancellazione di milioni di celle
                    # immediatamente prima della copia dello stesso rettangolo.
                    {'copyPaste': {'source': source_range, 'destination': target_range, 'pasteType': 'PASTE_NORMAL'}},
                    {'updateSheetProperties': {'properties': {'sheetId': target_id,
                       'gridProperties': {'frozenRowCount': 1}}, 'fields': 'gridProperties.frozenRowCount'}},
                    {'repeatCell': {'range': {'sheetId': target_id, 'startRowIndex': 0, 'endRowIndex': 1},
                       'cell': {'userEnteredFormat': {'textFormat': {'bold': True},
                                'backgroundColor': {'red': 0.85, 'green': 0.92, 'blue': 0.97}}},
                       'fields': 'userEnteredFormat'}},
                    {'setBasicFilter': {'filter': {'range': target_range}}},
                ])
                if name == 'Prezzi':
                    for label in ('Prezzo buylist (€)', 'Credito (€)'):
                        index = tables[name][0].index(label)
                        publish.append({'repeatCell': {'range': {'sheetId': target_id, 'startRowIndex': 1,
                                        'endRowIndex': grid['rowCount'], 'startColumnIndex': index, 'endColumnIndex': index + 1},
                            'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0.00'}}},
                            'fields': 'userEnteredFormat.numberFormat'}})
            publish.extend({'deleteSheet': {'sheetId': sheet_id}} for _, sheet_id in stages.values())
            print('Google Sheets: pubblico i dati verificati nelle schede visibili.', flush=True)
            for attempt in range(3):
                try:
                    self.batch(publish)
                    break
                except SheetsError as exc:
                    # Prima di ripetere controlla sia il marker sia i temporanei.
                    # Il batch atomico elimina tutti i temporanei al successo:
                    # se esistono ancora tutti, non è stato applicato.
                    try:
                        if self.published(run_id, prefix):
                            break
                    except SheetsError:
                        pass
                    observed = {s['properties']['sheetId']: s['properties']['title']
                                for s in self.metadata().get('sheets', [])}
                    not_applied = all(observed.get(sheet_id) == title for title, sheet_id in stages.values())
                    if not exc.retryable or not not_applied or attempt == 2:
                        raise
                    print(f'Google Sheets: errore temporaneo alla pubblicazione; tentativo {attempt + 2}/3 senza ricaricare i blocchi.', flush=True)
                    time.sleep(5 * (2 ** attempt))
            if not self.published(run_id, prefix):
                raise SheetsError('Pubblicazione Google Sheets non confermata dal marker finale.')
        except Exception:
            # Rimuove esclusivamente i temporanei marcati di questa importazione.
            try:
                cleanup = []
                for sheet in self.metadata().get('sheets', []):
                    if any(m.get('metadataKey') == 'cgc_stage' and m.get('metadataValue') == run_id
                           for m in sheet.get('developerMetadata', [])):
                        cleanup.append({'deleteSheet': {'sheetId': sheet['properties']['sheetId']}})
                if cleanup:
                    self.batch(cleanup)
            except Exception:
                pass
            raise
        kind = ' PARZIALE' if prefix == 'CGC_PARZIALE_' else ''
        print(f'IMPORTAZIONE GOOGLE SHEETS{kind} COMPLETATA: {len(tables["Prezzi"]) - 1} voci.', flush=True)


def authenticated_session():
    import google.auth
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    raw = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    if raw:
        try:
            info = json.loads(raw)
            if info.get('type') != 'service_account':
                raise ValueError('tipo')
            # Non accetta endpoint arbitrari dentro un JSON di credenziali.
            if info.get('token_uri') != 'https://oauth2.googleapis.com/token':
                raise ValueError('endpoint')
            credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        except (ValueError, TypeError, AttributeError) as exc:
            raise SheetsError('GOOGLE_SERVICE_ACCOUNT_JSON non valido. Non incollare la chiave nei log o nel codice.') from exc
    else:
        try:
            credentials, _ = google.auth.default(scopes=[SCOPE])
        except google.auth.exceptions.DefaultCredentialsError as exc:
            raise SheetsError('Credenziali Google mancanti: configura GOOGLE_SERVICE_ACCOUNT_JSON nei Secrets GitHub.') from exc
    return AuthorizedSession(credentials)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='CardGameCorner_Buylist_Pokemon_Completa.xlsx')
    parser.add_argument('--spreadsheet-id', default=os.environ.get('GOOGLE_SHEET_ID', '').strip() or DEFAULT_SPREADSHEET_ID)
    parser.add_argument('--check', action='store_true', help='Verifica credenziali e accesso, senza modificare il foglio')
    parser.add_argument('--partial', action='store_true', help='Consente un file parziale, usando schede CGC_PARZIALE_ separate')
    args = parser.parse_args()
    try:
        sheet_id = spreadsheet_id(args.spreadsheet_id)
        # Valida l'Excel prima di autenticare se si sta importando.
        tables = None if args.check else load_tables(Path(args.input), allow_partial=args.partial)
        with authenticated_session() as session:
            client = SheetsClient(sheet_id, session)
            if args.check:
                client.metadata()
                print('Google Sheets: credenziali e lettura del documento verificate.', flush=True)
            else:
                info = {row[0]: row[1] for row in tables['Info'][1:] if len(row) >= 2}
                prefix = 'CGC_PARZIALE_' if info.get('Stato') == 'PARZIALE' else 'CGC_'
                client.publish(tables, prefix=prefix)
    except (SheetsError, OSError, ValueError) as exc:
        print(f'IMPORTAZIONE GOOGLE SHEETS INTERROTTA: {exc}', file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        # Nessun dump di credenziali, token o contenuto delle risposte OAuth.
        print(f'IMPORTAZIONE GOOGLE SHEETS INTERROTTA ({type(exc).__name__}). Verifica credenziali e accesso.',
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
