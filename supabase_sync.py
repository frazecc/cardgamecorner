"""Importa un checkpoint COMPLETO in Supabase, senza ripetere lo scraping.

Le carte sono confrontate a blocchi e si conservano temporaneamente soltanto
quelle cambiate. L'archivio corrente cambia in una transazione dopo i controlli
finali. Le chiavi segrete si leggono soltanto dall'ambiente.
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from scraper import load_checkpoint, parse_variants, product_key, variant_signature, query_key

DEFAULT_URL = 'https://uzvvzuoorpdivanwbsen.supabase.co'


def prepare_card(row):
    key = product_key(row)
    raw = dict(row, product={k: v for k, v in row['product'].items() if k != 'Variants'})
    variants = [{'key': key + '|' + str(v['IdProduct']) + '|' +
                 hashlib.sha256(variant_signature(v).encode('utf-8')).hexdigest(), 'raw': v}
                for v in parse_variants(row)]
    return {'key': key, 'raw': raw, 'variants': variants}


def batches(cards, size=100, max_bytes=700_000):
    if size < 1:
        raise ValueError('Il blocco deve contenere almeno una carta.')
    block, length = [], 2
    for card in cards:
        n = len(json.dumps(card, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 1
        if block and (len(block) >= size or length + n > max_bytes):
            yield block
            block, length = [], 2
        block.append(card)
        length += n
    if block:
        yield block


def load_source(path):
    source = load_checkpoint(path)
    expected = {query_key(q['id']) for q in source['queries']}
    if set(source['completed']) != expected:
        raise ValueError('Checkpoint PARZIALE: non puo sostituire la buylist completa.')
    if Path(path).stat().st_size != source['valid_bytes']:
        raise ValueError('Checkpoint con coda incompleta: importazione interrotta.')
    prices = sum(len(parse_variants(row)) for row in source['records'].values())
    if not source['records'] or not prices:
        raise ValueError('Checkpoint senza carte o varianti.')
    return source, prices


class SupabaseClient:
    def __init__(self, url, secret, session=None):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.supabase.co')
                or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('SUPABASE_URL deve essere l\'URL HTTPS ufficiale del progetto.')
        if not secret or secret.startswith('sb_publishable_'):
            raise ValueError('Configura SUPABASE_SERVICE_ROLE_KEY come secret GitHub, mai nel codice.')
        self.url = url.rstrip('/')
        self.session = session or requests.Session()
        self.headers = {'apikey': secret, 'Content-Type': 'application/json'}
        # Le nuove secret key non sono JWT: vanno solo nell'header apikey.
        if not secret.startswith('sb_secret_'):
            self.headers['Authorization'] = 'Bearer ' + secret

    def rpc(self, name, body):
        for attempt in range(6):
            try:
                response = self.session.post(self.url + '/rest/v1/rpc/' + name,
                    headers=self.headers, json=body, timeout=120)
                if response.ok:
                    return response.json() if response.content else None
                retry = response.status_code in (429, 500, 502, 503, 504)
                if not retry:
                    raise RuntimeError(f'Supabase HTTP {response.status_code} in {name}; dati non pubblicati.')
            except requests.RequestException:
                if attempt == 5:
                    raise RuntimeError('Supabase non raggiungibile dopo i tentativi previsti.') from None
            if attempt == 5:
                raise RuntimeError('Supabase temporaneamente indisponibile; riprova lo stesso checkpoint.')
            time.sleep(min(2 ** attempt, 20))


def sync(path, source_run_id='', batch_size=100, client=None):
    source, price_count = load_source(path)
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    import_id = 'cgc-' + digest[:32]
    client = client or SupabaseClient(os.environ.get('SUPABASE_URL') or DEFAULT_URL,
                                    os.environ.get('SUPABASE_SERVICE_ROLE_KEY', ''))
    args = {'p_id': import_id, 'p_source_run_id': source_run_id or digest,
            'p_cards': len(source['records']), 'p_prices': price_count, 'p_queries': len(source['queries'])}
    state = client.rpc('cgc_delta_open', args)
    # Un import gia completo e un no-op verificato.
    if state != 'COMPLETE':
        confirmed = 0
        for block in batches((prepare_card(r) for r in source['records'].values()), size=batch_size):
            n = client.rpc('cgc_delta_ingest', {'p_id': import_id, 'p_batch': block})
            if n != len(block):
                raise RuntimeError('Numero di carte confermate diverso dal blocco inviato.')
            confirmed += n
            print(f'Supabase: {confirmed}/{len(source["records"])} carte confermate.', flush=True)
    result = client.rpc('cgc_delta_complete', {'p_id': import_id})
    if not isinstance(result, dict) or result.get('cards') != len(source['records']) or result.get('prices') != price_count or result.get('state') != 'COMPLETE':
        raise RuntimeError('Conferma finale Supabase non valida.')
    print(f'SUPABASE COMPLETO: {result["cards"]} carte, {result["prices"]} voci prezzo.', flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', default='CardGameCorner_Buylist_Pokemon_Completa.checkpoint.jsonl')
    parser.add_argument('--source-run-id', default=os.environ.get('GITHUB_RUN_ID', ''))
    parser.add_argument('--batch-size', type=int, default=100)
    args = parser.parse_args()
    sync(args.checkpoint, args.source_run_id, args.batch_size)
