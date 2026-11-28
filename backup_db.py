#!/usr/bin/env python3
"""Copia di sicurezza del database (carte e prezzi dell'archivio attivo) in un file compresso.

Lo esegue ogni notte il workflow "Backup del database" (.github/workflows/backup.yml); il file resta
tra gli artefatti del workflow per 3 giorni e poi GitHub lo cancella da solo.

Contenuto di backup/cgc_backup.jsonl.gz: una riga JSON per carta e una per prezzo, con tutte le colonne.
  {"t": "cards", "row": {...}}   {"t": "prices", "row": {...}}
backup/manifest.json riporta data, archivio e conteggi. Serve SUPABASE_URL e il secret SUPABASE_SERVICE_ROLE_KEY.
"""
import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from esporta_json import SupabaseReader, _get

PAGE = 1000
TABLES = (('cards', 'cgc_cards', 'card_key'), ('prices', 'cgc_prices', 'entry_key'))


def dump_table(client, table, key, import_id, write):
    """Legge una tabella a blocchi (per chiave, senza OFFSET) e scrive ogni riga. Restituisce il numero di righe."""
    count, last = 0, None
    while True:
        params = {'select': '*', 'import_id': 'eq.' + import_id, 'order': key + '.asc', 'limit': str(PAGE)}
        if last is not None:
            params[key] = 'gt.' + last
        block = _get(client, table, params)
        if not block:
            return count
        for row in block:
            write(row)
        count += len(block)
        last = block[-1][key]
        if len(block) < PAGE:
            return count


def run(client, out_dir, now=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    active = _get(client, 'cgc_active', {'select': 'import_id', 'singleton': 'eq.true', 'limit': '1'})
    if not active:
        raise RuntimeError('Nessun archivio attivo: nessun backup creato.')
    import_id = active[0]['import_id']
    counts = {}
    with gzip.open(out / 'cgc_backup.jsonl.gz', 'wt', encoding='utf-8', compresslevel=6) as stream:
        for name, table, key in TABLES:
            counts[name] = dump_table(client, table, key, import_id,
                                      lambda row, name=name: stream.write(
                                          json.dumps({'t': name, 'row': row}, ensure_ascii=False, separators=(',', ':')) + '\n'))
            print(f'  {name}: {counts[name]} righe', flush=True)
    if not counts['cards'] or not counts['prices']:
        raise RuntimeError('Backup vuoto: lo scarto del file e voluto, controlla il database.')
    manifest = {'creato_il': (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'import_id': import_id, 'righe': counts}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default='backup')
    args = parser.parse_args()
    if not os.environ.get('SUPABASE_URL'):
        sys.exit('Manca la variabile GitHub SUPABASE_URL.')
    client = SupabaseReader(os.environ['SUPABASE_URL'], os.environ.get('SUPABASE_SERVICE_ROLE_KEY', ''))
    manifest = run(client, args.out)
    size = Path(args.out, 'cgc_backup.jsonl.gz').stat().st_size / 1e6
    print(f"Backup pronto: {manifest['righe']['cards']} carte, {manifest['righe']['prices']} prezzi, {size:.1f} MB", flush=True)


if __name__ == '__main__':
    main()
