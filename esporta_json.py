#!/usr/bin/env python3
"""Genera il sito statico (docs/ + data/*.json) leggendo la buylist da Supabase.

Lo esegue ogni ora il workflow "Pubblica il sito e i dati" (.github/workflows/pagine.yml).
Il sito NON interroga piu il database: legge solo questi file, che stanno accanto alla pagina.

File prodotti nella cartella di uscita (default: sito/):
  index.html, style.css, *.js ...   copia di docs/
  data/prezzi.json                  stato PREZZO DISPONIBILE
  data/richiesta.json               stato VALUTAZIONE SU RICHIESTA
  data/escluse.json                 stato ESCLUSA DALLA BUYLIST
  data/senza-prezzo.json            stato SENZA PREZZO BUYLIST
  data/meta.json                    data di aggiornamento, conteggi e valori per le caselle dei filtri

Formato di ogni file dati (raggruppato per carta, letto da docs/dati-buylist.js):
  {"campi_variante": [...], "carte": [{"k": card_key, "n": nome, "en": nome alternativo,
                                       "e": espansione, "s": numero, "i": immagine,
                                       "v": [[valore per ogni campo_variante], ...]}]}

Servono la variabile SUPABASE_URL e il secret SUPABASE_SERVICE_ROLE_KEY (mai nel codice).
Se qualcosa non va lo script si ferma con un errore e il sito gia pubblicato resta com'e.
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GROUP_OF_STATUS = {
    'PREZZO DISPONIBILE': 'prezzi',
    'VALUTAZIONE SU RICHIESTA': 'richiesta',
    'SENZA PREZZO BUYLIST': 'senza-prezzo',
    'ESCLUSA DALLA BUYLIST': 'escluse',
}
GROUP_NAMES = ['prezzi', 'richiesta', 'escluse', 'senza-prezzo']
VARIANT_FIELDS = ['language', 'condition', 'foil', 'first_edition', 'buylist', 'credit', 'status']
FILTER_FIELDS = ['language', 'condition', 'foil', 'first_edition']
SELECT = ('entry_key,card_key,name,name_en,expansion,serial_number,image,'
          'language,condition,foil,first_edition,buylist,credit,status')
PAGE_SIZE = 1000


def text(value):
    return '' if value is None else str(value)


def number(value):
    """Numero (int se intero) oppure None. Accetta anche testo con virgola decimale."""
    if value is None or str(value).strip() == '':
        return None
    n = float(str(value).replace(',', '.'))
    return int(n) if n.is_integer() else n


def build_groups(rows):
    """Divide le righe (una per variante) nei 4 gruppi di stato, raggruppando le varianti per carta."""
    cards = {name: {} for name in GROUP_NAMES}
    for r in rows:
        group = GROUP_OF_STATUS.get(r.get('status'))
        if group is None:
            raise ValueError('Stato offerta sconosciuto: %r (riga %s)' % (r.get('status'), r.get('entry_key')))
        card = cards[group].get(r['card_key'])
        if card is None:
            card = cards[group][r['card_key']] = {
                'k': r['card_key'], 'n': r.get('name'), 'en': r.get('name_en'), 'e': r.get('expansion'),
                's': r.get('serial_number'), 'i': r.get('image'), 'v': []}
        card['v'].append([text(r.get('language')), text(r.get('condition')), text(r.get('foil')),
                          text(r.get('first_edition')), number(r.get('buylist')), number(r.get('credit')),
                          r['status']])
    groups = {}
    for name in GROUP_NAMES:
        ordered = sorted(cards[name].values(),
                         key=lambda c: (text(c['n']).casefold(), text(c['e']).casefold(), text(c['s']), c['k']))
        groups[name] = {'campi_variante': VARIANT_FIELDS, 'carte': ordered}
    return groups


def filter_values(group):
    """Per ogni campo dei filtri: [[valore, quante righe], ...] ('' = campo vuoto)."""
    index = {f: VARIANT_FIELDS.index(f) for f in FILTER_FIELDS}
    counts = {f: {} for f in FILTER_FIELDS}
    for card in group['carte']:
        for variant in card['v']:
            for f, i in index.items():
                counts[f][variant[i]] = counts[f].get(variant[i], 0) + 1
    return {f: [[v, n] for v, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))] for f, c in counts.items()}


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(',', ':'))


def write_site(groups, rows, checked, docs_dir, out_dir, now=None):
    """Copia docs/ in out_dir e scrive data/*.json. checked = {'dal': ..., 'fino_al': ...} oppure None."""
    docs_dir, out_dir = Path(docs_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(docs_dir, out_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns('data', '__pycache__'))
    (out_dir / '.nojekyll').write_text('', encoding='utf-8')
    for name in GROUP_NAMES:
        dump(out_dir / 'data' / (name + '.json'), groups[name])
    now = now or datetime.now(timezone.utc)
    meta = {
        'generato_il': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'conteggi': {'righe': len(rows), 'per_gruppo': {
            n: {'carte': len(groups[n]['carte']), 'righe': sum(len(c['v']) for c in groups[n]['carte'])}
            for n in GROUP_NAMES}},
        'valori_filtri': {n: filter_values(groups[n]) for n in GROUP_NAMES},
    }
    if checked and checked.get('dal') and checked.get('fino_al'):
        meta['dati_controllati_dal'] = checked['dal']
        meta['dati_controllati_fino_al'] = checked['fino_al']
    dump(out_dir / 'data' / 'meta.json', meta)
    return meta


# ----------------------------------------------------------------------------- lettura da Supabase

class SupabaseReader:
    """Client minimo di sola lettura (serve solo `requests`; non carica scraper, pandas o Playwright)."""

    def __init__(self, url, secret, session=None):
        import requests
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.supabase.co')
                or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError("SUPABASE_URL deve essere l'URL HTTPS ufficiale del progetto.")
        if not secret or secret.startswith('sb_publishable_'):
            raise ValueError('Configura SUPABASE_SERVICE_ROLE_KEY come secret GitHub, mai nel codice.')
        self.url = url.rstrip('/')
        self.session = session or requests.Session()
        self.headers = {'apikey': secret, 'Accept': 'application/json'}
        # Le nuove secret key (sb_secret_...) non sono JWT: vanno solo nell'header apikey.
        if not secret.startswith('sb_secret_'):
            self.headers['Authorization'] = 'Bearer ' + secret

def _get(client, path, params, tries=6):
    import requests
    for attempt in range(tries):
        try:
            response = client.session.get(client.url + '/rest/v1/' + path, headers=client.headers,
                                          params=params, timeout=120)
            if response.ok:
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError('Supabase HTTP %s leggendo %s: %s' % (response.status_code, path, response.text[:200]))
        except requests.RequestException:
            pass
        if attempt == tries - 1:
            raise RuntimeError('Supabase non raggiungibile o troppo lento leggendo %s. Nulla e stato pubblicato.' % path)
        time.sleep(min(2 ** attempt, 20))


def fetch_rows(client):
    """Legge TUTTE le righe della vista cgc_buylist a blocchi, in ordine di entry_key."""
    rows, last, seen = [], None, set()
    while True:
        params = {'select': SELECT, 'order': 'entry_key.asc', 'limit': str(PAGE_SIZE)}
        if last is not None:
            params['entry_key'] = 'gt.' + last
        block = _get(client, 'cgc_buylist', params)
        if not block:
            return rows
        for r in block:
            if r['entry_key'] in seen:
                raise RuntimeError('Riga duplicata in cgc_buylist: %s. Nulla e stato pubblicato.' % r['entry_key'])
            seen.add(r['entry_key'])
        rows.extend(block)
        last = block[-1]['entry_key']
        if (len(rows) // PAGE_SIZE) % 20 == 0:
            print('Letture da Supabase: %d righe...' % len(rows), flush=True)
        if len(block) < PAGE_SIZE:
            return rows


def fetch_checked_range(client):
    """Data della carta controllata da piu tempo e di quella controllata piu di recente (facoltativo)."""
    try:
        active = _get(client, 'cgc_active', {'select': 'import_id', 'singleton': 'eq.true', 'limit': '1'})
        base = {'select': 'checked_at', 'checked_at': 'not.is.null', 'limit': '1'}
        if active:
            base['import_id'] = 'eq.' + active[0]['import_id']
        oldest = _get(client, 'cgc_cards', dict(base, order='checked_at.asc'))
        newest = _get(client, 'cgc_cards', dict(base, order='checked_at.desc'))
        if oldest and newest:
            return {'dal': oldest[0]['checked_at'], 'fino_al': newest[0]['checked_at']}
    except Exception as err:  # e solo un'informazione in piu: non deve bloccare la pubblicazione
        print('Date di controllo non disponibili (%s): le salto.' % err, flush=True)
    return None


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default=str(root / 'sito'), help='cartella di uscita (default: sito/)')
    parser.add_argument('--docs', default=str(root / 'docs'), help='cartella del sito (default: docs/)')
    parser.add_argument('--min-righe', type=int, default=1000,
                        help='sotto questo numero di righe non pubblica nulla (protezione da database vuoto)')
    args = parser.parse_args(argv)

    url = os.environ.get('SUPABASE_URL', '')
    if not url:
        sys.exit('Manca la variabile GitHub SUPABASE_URL (Settings > Secrets and variables > Actions > Variables).')
    client = SupabaseReader(url, os.environ.get('SUPABASE_SERVICE_ROLE_KEY', ''))

    rows = fetch_rows(client)
    print('Righe lette: %d' % len(rows), flush=True)
    if len(rows) < args.min_righe:
        sys.exit('Troppe poche righe (%d, minimo %d): non pubblico nulla per non svuotare il sito.' % (len(rows), args.min_righe))
    groups = build_groups(rows)
    meta = write_site(groups, rows, fetch_checked_range(client), args.docs, args.out)
    for name in GROUP_NAMES:
        size = Path(args.out, 'data', name + '.json').stat().st_size
        print('  %-13s %7d carte, %7d righe, %6.2f MB' % (
            name, meta['conteggi']['per_gruppo'][name]['carte'], meta['conteggi']['per_gruppo'][name]['righe'], size / 1e6))
    print('Sito pronto in %s (dati del %s)' % (args.out, meta['generato_il']))


if __name__ == '__main__':
    main()
