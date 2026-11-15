"""Esporta l'intera ricerca Pokémon della buylist, seguendo la paginazione reale.

Dipendenze: pip install playwright pandas openpyxl
Browser:    playwright install chromium
Avvio:      python scraper.py

Il foglio Prezzi riporta TUTTE le voci Variants restituite dal sito: lingua,
condizione, foil e prima edizione, con BuyList e Credit distinti. Le combinazioni
non pubblicate non vengono inventate. Carte contiene l'anagrafica dei prodotti.
Dati_originali conserva tutti i campi dei prodotti raccolti dall'applicazione.
Non aggiunge nulla al carrello e non richiede un account.
"""

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.cardgamecorner.com/it/buylist-2026?game=pokemon"
OUTPUT = "CardGameCorner_Buylist_Pokemon_Completa.xlsx"
VERSION = "5.0.1"
MORE = 'button[ng-click="showMore()"]'
PRODUCT = '[ng-repeat="product in expansion.Products"]'

# Legge gli stessi oggetti usati dai template Angular della pagina. Non inventa
# endpoint o selettori di prezzo e non interpreta div generici contenenti euro.
READ_STATE = r"""(options = {}) => {
    const input = document.querySelector('#simplesearch');
    if (!input || !window.angular) return null;
    const scope = angular.element(input).scope();
    if (!scope) throw Error('Scope Angular non disponibile: struttura del sito cambiata.');
    const root = document.querySelector('[ng-app]') || document.getElementById('ng-app');
    let pending = 0;
    try {
        const injector = angular.element(root || input).injector();
        pending = injector.get('$http').pendingRequests.length;
    } catch (e) {
        throw Error('Impossibile verificare le richieste Angular: ' + e.message);
    }
    const clean = value => {
        if (value === undefined) return null;
        return JSON.parse(JSON.stringify(value, (k,v) => {
            if (k.startsWith('$') || typeof v === 'function') return undefined;
            return v;
        }));
    };
    const seen = new Set(options.seen || []);
    const all_keys = [];
    const rows = [...document.querySelectorAll('[ng-repeat="product in expansion.Products"]')].map(el => {
        const child = angular.element(el).scope();
        if (!child || !child.product) throw Error('Prodotto senza dati Angular.');
        const text = selector => (el.querySelector(selector)?.textContent || '').trim();
        const expansion = child.expansion?.Expansion || '';
        const name = text('h4.name');
        const image = el.querySelector('img')?.getAttribute('data-ng-src') || el.querySelector('img')?.src || '';
        const product = child.product;
        const idField = ['Id', 'ProductId', 'ID', 'id', 'IdProduct'].find(k => product[k] != null);
        const token = idField ? idField + ':' + product[idField] : JSON.stringify([image, expansion, name]);
        all_keys.push(token);
        // Le pagine di showMore possono contenere anche tutte le carte precedenti.
        // Nei controlli frequenti non trasferire il grande JSON delle varianti.
        if (options.probe) return {product: idField ? {[idField]:product[idField]} : {}, name, image, expansion};
        if (seen.has(token)) return null;
        const selected = model => {
            const select = el.querySelector('select[ng-model="' + model + '"]');
            return select?.selectedOptions[0]?.textContent?.trim() || '';
        };
        const option_labels = model => [...(el.querySelector('select[ng-model="' + model + '"]')?.options || [])]
            .map(o=>o.textContent.trim());
        return {
            product: clean(child.product),
            expansion: child.expansion?.Expansion ||
                el.closest('[ng-repeat="expansion in Expansions"]')?.querySelector('.expansion-title')?.textContent.trim() || '',
            name: text('h4.name'),
            name_en: text('.alt-name'),
            image: el.querySelector('img')?.getAttribute('data-ng-src') || el.querySelector('img')?.src || '',
            price_text: text('.buy-price'),
            language: selected('product.Language'),
            condition: selected('product.Condition'),
            languages: option_labels('product.Language'),
            conditions: option_labels('product.Condition'),
            reverse: el.querySelector('input[ng-model="product.Reverse"]')?.checked || false,
            first_edition: el.querySelector('input[ng-model="product.firstEdition"]')?.checked || false,
            evaluation: el.innerText.includes('Riceverai una valutazione'),
        };
    }).filter(Boolean);
    return {
        page: Number(scope.PageNumber), pages: Number(scope.PageCount), pending,
        no_results: document.body.innerText.includes('Nessun prodotto corrisponde alla tua ricerca.'),
        data_empty: Array.isArray(scope.Expansions) && scope.Expansions.length === 0,
        // SearchMobile chiude i filtri e può rimuovere i select dal DOM.
        // L'elenco dei dati resta nello scope anche a pannello chiuso.
        editions: (scope.Filters?.Expansions || []).filter(exp=>exp.Id != null)
            .map(exp=>({id:exp.Id, name:exp.Espansione})),
        only_hot: scope.OnlyBuyList,
        filters: clean(scope.SelectedFilters || {}), rows, all_keys,
        more: [...document.querySelectorAll('button[ng-click="showMore()"]')]
            .some(e=>e.getClientRects().length > 0),
        counters: Object.fromEntries(Object.entries(scope).filter(([k,v]) =>
            /count|total/i.test(k) && typeof v === 'number'))
    };
}"""


def product_key(row):
    product = row['product']
    for name in ('Id', 'ProductId', 'ID', 'id', 'IdProduct'):
        if product.get(name) is not None:
            return f'{name}:{product[name]}'
    # L'immagine distingue anche carte con stesso nome e stessa espansione.
    if not row['image'] or not row['name']:
        raise RuntimeError('Carta priva di identificatore e immagine: impossibile deduplicare in sicurezza.')
    data = [row['image'], row['expansion'], row['name']]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def fingerprint(state):
    if 'all_keys' in state:
        return tuple(state['all_keys'])
    return tuple(product_key(row) for row in state['rows'])


class SearchMonitor:
    """Distingue una risposta vuota riuscita da un errore di rete/HTTP."""
    def __init__(self, page):
        self.successful_json = 0
        self.successful_responses = 0
        self.errors = []
        self.active = False
        page.on('response', self.response)
        page.on('requestfailed', self.failed)

    def begin(self):
        self.successful_json = 0
        self.successful_responses = 0
        self.errors = []
        self.active = True

    def relevant(self, request):
        return (self.active and request.resource_type in ('xhr', 'fetch')
                and urlparse(request.url).hostname == urlparse(URL).hostname)

    def response(self, response):
        if not self.relevant(response.request):
            return
        if response.status >= 400:
            self.errors.append(f'HTTP {response.status}: {urlparse(response.url).path}')
        else:
            self.successful_responses += 1
            if 'json' in response.headers.get('content-type', '').lower():
                self.successful_json += 1

    def failed(self, request):
        if self.relevant(request):
            self.errors.append(f'Richiesta fallita: {urlparse(request.url).path}')


def start_search(page):
    page._buylist_monitor.begin()
    page.locator('#button-search').click()


def wait_ready(page, timeout, previous=None, expected_page=None, edition=None, require_editions=False, seen=None):
    deadline = time.monotonic() + timeout
    stable = None
    since = None
    last = None
    while time.monotonic() < deadline:
        state = page.evaluate(READ_STATE, {'probe': True})
        last = state
        monitor = getattr(page, '_buylist_monitor', None)
        if monitor is not None and monitor.errors:
            raise RuntimeError('Errore nel caricamento: ' + '; '.join(monitor.errors))
        current_edition = (state or {}).get('filters', {}).get('edition')
        edition_ok = str(current_edition or '') == str(edition or '')
        if not state or (require_editions and not state['editions']):
            stable, since = None, None
            page.wait_for_timeout(250)
            continue
        if state['pending'] != 0 or not edition_ok:
            stable, since = None, None
            page.wait_for_timeout(250)
            continue
        # TotalRecords è il contatore del risultato, mentre Expansions può
        # restare un oggetto/valore precedente anche con zero risultati.
        total_records = state.get('counters', {}).get('TotalRecords')
        successful_responses = (getattr(monitor, 'successful_responses', 0)
                                if monitor is not None else 0)
        empty_response = (state['pages'] == 0 and total_records == 0
                          and monitor is not None and successful_responses > 0)
        response_ok = monitor is None or successful_responses > 0
        text_empty = (state['no_results'] and state['pages'] == 0
                      and total_records in (None, 0))
        if (text_empty or empty_response) and not state['rows'] and response_ok:
            marker = ('empty', str(edition), state['page'], state['pages'])
            if marker != stable:
                stable, since = marker, time.monotonic()
            elif time.monotonic() - since >= 2.0:
                state['empty_confirmed'] = True
                return state
            page.wait_for_timeout(250)
            continue
        if state['rows'] and response_ok:
            if state['pages'] < 1 or state['page'] < 1:
                stable, since = None, None
                page.wait_for_timeout(250)
                continue
            signature = fingerprint(state)
            changed = previous is None or signature != previous
            correct_page = expected_page is None or state['page'] == expected_page
            if changed and correct_page:
                marker = (state['page'], state['pages'], signature)
                if marker != stable:
                    stable, since = marker, time.monotonic()
                elif time.monotonic() - since >= 1.0:
                    # Una sola lettura completa dopo il caricamento, con solo le
                    # carte non ancora elaborate nella ricerca corrente.
                    full = page.evaluate(READ_STATE, {'seen': list(seen or ())})
                    if (full and full['pending'] == 0 and full['page'] == state['page']
                            and full['pages'] == state['pages'] and full['filters'] == state['filters']
                            and fingerprint(full) == signature):
                        return full
                    stable, since = None, None
                page.wait_for_timeout(250)
                continue
        stable, since = None, None
        last = state
        page.wait_for_timeout(250)
    detail = {k: last.get(k) for k in ('page', 'pages', 'pending')} if last else {}
    raise RuntimeError(f'Caricamento non completato entro {timeout}s: {detail}. Nessuna pagina saltata.')


def validate_state(state, edition=None):
    if state['only_hot'] is not False:
        raise RuntimeError('Il filtro Hot Cards non è impostato su Tutto.')
    active = {k: v for k, v in state['filters'].items() if v not in (None, '', False, [])}
    expected = {} if edition is None else {'edition': edition}
    if {k: str(v) for k, v in active.items()} != {k: str(v) for k, v in expected.items()}:
        raise RuntimeError(f'Filtri diversi dalla ricerca attesa: {active}; attesi: {expected}')
    if (state['no_results'] or state.get('empty_confirmed')) and not state['rows']:
        return
    if not 1 <= state['page'] <= state['pages']:
        raise RuntimeError('Numeri di pagina non validi.')


VARIANT_DIMENSIONS = ('Language', 'Condition', 'Foil', 'FirstEdition')


def parse_variants(row):
    """Legge i prezzi originali, senza calcoli per condizione o lingua."""
    value = row['product'].get('Variants')
    try:
        variants = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"Variants non leggibile per {product_key(row)}") from exc
    if not isinstance(variants, list):
        raise RuntimeError(f"Elenco Variants mancante per {product_key(row)}")
    unique = {}
    for variant in variants:
        if not isinstance(variant, dict):
            raise RuntimeError(f"Variante non valida per {product_key(row)}")
        required = ('IdProduct', *VARIANT_DIMENSIONS, 'BuyList', 'Credit', 'Evaluation', 'NoBuyList')
        if any(field not in variant or variant[field] is None for field in required):
            raise RuntimeError(f"Campi variante mancanti per {product_key(row)}")
        if not str(variant['IdProduct']).strip():
            raise RuntimeError('ID variante vuoto.')
        if any(not isinstance(variant[field], str) for field in VARIANT_DIMENSIONS):
            raise RuntimeError(f"Dimensioni variante non valide: {variant['IdProduct']}")
        if not variant['Language'].strip() or not variant['Condition'].strip():
            raise RuntimeError(f"Lingua/condizione assente: {variant['IdProduct']}")
        for field in ('BuyList', 'Credit'):
            number = variant[field]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                raise RuntimeError(f"Prezzo {field} non valido: {variant['IdProduct']}")
        if any(not isinstance(variant[field], bool) for field in ('Evaluation', 'NoBuyList')):
            raise RuntimeError(f"Stato variante non valido: {variant['IdProduct']}")
        key = str(variant['IdProduct'])
        if key in unique and unique[key] != variant:
            raise RuntimeError(f'ID variante duplicato con dati diversi: {key}')
        unique[key] = variant
    return list(unique.values())


def merge_product_row(old, row):
    """Unisce le varianti dello stesso prodotto incontrate in più ricerche."""
    current = parse_variants(row)
    if old is None:
        return row
    if (old['image'], old['name']) != (row['image'], row['name']):
        raise RuntimeError(f'Identificatore ambiguo: {product_key(row)}. Nessuna fusione automatica.')
    merged = {str(v['IdProduct']): v for v in parse_variants(old)}
    for variant in current:
        key = str(variant['IdProduct'])
        previous = merged.get(key)
        if previous and any(previous[field] != variant[field] for field in VARIANT_DIMENSIONS):
            raise RuntimeError(f'ID variante con lingua/condizione/edizione diversa: {key}')
        # Se il prezzo cambia durante la scansione conserva l'ultima osservazione.
        merged[key] = variant
    result = dict(row, product=dict(row['product']))
    result['product']['Variants'] = json.dumps(list(merged.values()), ensure_ascii=False)
    return result


def price_rows(records):
    prices = []
    for key, row in records.items():
        for variant in parse_variants(row):
            if variant['NoBuyList']:
                status = 'ESCLUSA DALLA BUYLIST'
            elif variant['Evaluation']:
                status = 'VALUTAZIONE SU RICHIESTA'
            elif variant['BuyList'] > 0 or variant['Credit'] > 0:
                status = 'PREZZO DISPONIBILE'
            else:
                status = 'SENZA PREZZO BUYLIST'
            prices.append({
                'ID carta': key,
                'ID variante': str(variant['IdProduct']),
                'Carta': row['name'],
                'Espansione': row['expansion'],
                'Numero carta': row['product'].get('SerialNumber'),
                'Lingua': variant['Language'],
                'Condizione (codice sito)': variant['Condition'],
                'Foil (dato sito)': variant['Foil'],
                'Prima edizione (dato sito)': variant['FirstEdition'],
                'Prezzo buylist (€)': variant['BuyList'],
                'Credito (€)': variant['Credit'],
                'Stato': status,
                'Valutazione su richiesta': variant['Evaluation'],
                'Esclusa dalla buylist': variant['NoBuyList'],
                'BuyListLock (dato sito)': variant.get('BuyListLock'),
                'Quantità (dato sito)': variant.get('Quantity'),
                'Immagine': row['image'],
            })
    return prices


def save_excel(path, records, pages_log, complete, error='', queries_log=None):
    queries_log = queries_log or []
    prices = price_rows(records)
    cards, raw = [], []
    for key, row in records.items():
        p = row['product']
        cards.append({
            'ID': key,
            'Carta': row['name'],
            'Nome alternativo': row['name_en'],
            'Espansione': row['expansion'],
            'Numero carta': p.get('SerialNumber'),
            'Numero varianti': len(parse_variants(row)),
            'Lingue nelle varianti': ', '.join(dict.fromkeys(v['Language'] for v in parse_variants(row))),
            'Condizioni nelle varianti': ', '.join(dict.fromkeys(v['Condition'] for v in parse_variants(row))),
            'Immagine': row['image'],
        })
        payload = json.dumps(p, ensure_ascii=False, sort_keys=True)
        # Excel limita le celle a 32767 caratteri: non troncare i dati originali.
        for i in range(0, len(payload), 30000):
            raw.append({'ID': key, 'Parte JSON': i // 30000 + 1, 'Dati JSON': payload[i:i+30000]})
    info = [
        {'Voce': 'Stato', 'Valore': 'COMPLETO: ricerca generale e tutte le espansioni percorse' if complete else 'PARZIALE'},
        {'Voce': 'Fonte', 'Valore': URL},
        {'Voce': 'Versione scraper', 'Valore': VERSION},
        {'Voce': 'Data UTC', 'Valore': datetime.now(timezone.utc).isoformat()},
        {'Voce': 'Carte distinte', 'Valore': len(cards)},
        {'Voce': 'Varianti nel foglio Prezzi', 'Valore': len(prices)},
        {'Voce': 'Carte senza varianti pubblicate', 'Valore': sum(not parse_variants(row) for row in records.values())},
        {'Voce': 'Pagine caricate', 'Valore': len(pages_log)},
        {'Voce': 'Ricerche completate', 'Valore': sum(q['Stato'] == 'COMPLETATA' for q in queries_log)},
        {'Voce': 'Prezzi', 'Valore': 'Tutte le voci Variants pubblicate: BuyList e Credit originali per lingua, condizione, foil e prima edizione. Nessun moltiplicatore o combinazione inventata.'},
        {'Voce': 'Codici condizione', 'Valore': 'NM/M = Near Mint; SP = Excellent; HP = Heavily Played; D = Poor/Damaged (etichette dei selettori del sito).'},
        {'Voce': 'Righe senza prezzo', 'Valore': 'Conservate anche valutazioni su richiesta, esclusioni e prezzi zero. Zero non è una quotazione positiva.'},
        {'Voce': 'ID variante', 'Valore': 'Ogni IdProduct resta distinto, anche con uguale lingua/condizione. Se ritrovato con gli stessi attributi, si conserva il prezzo dell’ultima osservazione.'},
        {'Voce': 'Copertura', 'Valore': 'Ricerca generale e ricerca separata per ogni espansione disponibile; tutte le pagine di ciascuna ricerca.'},
        {'Voce': 'Errore', 'Valore': error},
    ]
    # Le stringhe provenienti dal sito restano testo, anche se iniziano con '='.
    def frame(items):
        df = pd.DataFrame(items)
        for col in df.columns:
            df[col] = df[col].map(lambda x: "'" + x if isinstance(x, str) and x.startswith(('=', '+', '-', '@')) else x)
        return df
    temp = path.with_name(path.stem + '.tmp.xlsx')
    with pd.ExcelWriter(temp, engine='openpyxl') as writer:
        for name, items in [('Prezzi', prices), ('Carte', cards), ('Pagine', pages_log), ('Info', info), ('Espansioni', queries_log), ('Dati_originali', raw)]:
            frame(items).to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = min(65, max(14, len(str(column[0].value or '')) + 3))
                if column[0].value in ('Prezzo buylist (€)', 'Credito (€)'):
                    for cell in column[1:]:
                        cell.number_format = '#,##0.00'
    temp.replace(path)


def scrape(output, timeout=120, headed=False, partial_interval=900, load_images=False):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem.replace('_Completa', '') + '_PARZIALE.xlsx')
    checkpoint = output.with_suffix('.checkpoint.jsonl')
    records, log, queries_log = {}, [], []
    last_partial = time.monotonic()
    print(f'CardGameCorner scraper {VERSION} | {URL}', flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        if not load_images:
            # Gli URL delle immagini restano nei dati esportati; per raccogliere
            # i prezzi non servono i file grafici, i video o i font scaricati.
            page.route('**/*', lambda route: route.abort() if route.request.resource_type
                       in ('image', 'media', 'font') else route.continue_())
        page.set_default_timeout(timeout * 1000)
        page._buylist_monitor = SearchMonitor(page)
        try:
            page.goto(URL, wait_until='domcontentloaded', timeout=timeout * 1000)
            reject = page.get_by_role('button', name='Rifiuta tutto', exact=True)
            try:
                reject.wait_for(state='visible', timeout=10000)
                reject.click()
            except Exception:
                if reject.is_visible():
                    raise
            page.locator('#simplesearch').wait_for(state='visible')
            page.wait_for_function("""() => {
                const el = document.querySelector('#simplesearch');
                if (!el || !window.angular) return false;
                const s = angular.element(el).scope();
                return s && Array.isArray(s.BuylistSmallProducts) && s.BuylistSmallProducts.length > 0;
            }""", timeout=timeout * 1000)
            page.locator('#simplesearch').fill('')
            page.locator('select[ng-model="OnlyBuyList"]').select_option(label='Tutto')
            start_search(page)
            state = wait_ready(page, timeout, expected_page=1, require_editions=True)
            editions = state['editions']
            if not editions:
                raise RuntimeError('Nessuna espansione disponibile: non posso certificare la copertura.')
            queries = [{'id': None, 'name': 'Ricerca generale'}] + editions
            edition_selector = page.locator('select[ng-options*="Filters.Expansions"]')
            with checkpoint.open('w', encoding='utf-8') as journal:
                journal.write(json.dumps({'version': VERSION, 'url': URL, 'ricerche': queries}, ensure_ascii=False) + '\n')
                for query_index, query in enumerate(queries):
                    edition = query['id']
                    query_log = {'Espansione': query['name'], 'ID espansione': edition,
                                 'Stato': 'IN CORSO', 'Pagine': 0, 'Carte nella ricerca': 0}
                    queries_log.append(query_log)
                    print(f"Ricerca {query_index + 1}/{len(queries)}: {query['name']}", flush=True)
                    if edition is not None:
                        # La ricerca chiude i filtri: riaprirli quando serve.
                        if not edition_selector.is_visible():
                            page.locator('#btn-filters').click()
                        edition_selector.wait_for(state='visible', timeout=timeout * 1000)
                        # Angular può codificare lo stesso ID come number:123,
                        # string:123 o valore semplice. Usare il valore reale.
                        option_value = edition_selector.evaluate("""(el, id) => {
                            const matches = [...el.options].filter(o => {
                                const value = o.value.replace(/^(number|string):/, '');
                                return value === String(id);
                            });
                            if (matches.length !== 1) throw Error('Espansione non selezionabile: ' + id);
                            return matches[0].value;
                        }""", edition)
                        edition_selector.select_option(value=option_value)
                        start_search(page)
                        # Ricerche diverse possono avere risultati identici:
                        # il fingerprint è necessario solo nella paginazione.
                        state = wait_ready(page, timeout, expected_page=1, edition=edition)
                    validate_state(state, edition)
                    query_seen = set()
                    query_tokens = set()
                    expected_page = 1
                    expected_pages = state['pages']
                    while state['rows']:
                        validate_state(state, edition)
                        if state['page'] != expected_page or state['pages'] != expected_pages:
                            raise RuntimeError('Paginazione cambiata o non consecutiva. Risultato parziale.')
                        new_rows, updated_rows, new_local = [], [], 0
                        for row in state['rows']:
                            if not row['name']:
                                raise RuntimeError('Template di una carta non ancora caricato.')
                            key = product_key(row)
                            old = records.get(key)
                            merged = merge_product_row(old, row)
                            if key not in query_seen:
                                new_local += 1
                                query_seen.add(key)
                            if key not in records:
                                new_rows.append(row)
                            records[key] = merged
                            updated_rows.append(merged)
                        if new_local == 0:
                            raise RuntimeError('Pagina senza carte nuove nella ricerca corrente.')
                        item = {'Espansione ricercata': query['name'], 'Pagina': state['page'],
                                'Pagine totali': expected_pages, 'Nuove carte': len(new_rows),
                                'Carte nella ricerca': len(query_seen), 'Carte distinte cumulative': len(records),
                                'Contatori sito': json.dumps(state['counters'], ensure_ascii=False)}
                        log.append(item)
                        query_tokens.update(state.get('all_keys', ()))
                        query_log.update(Pagine=expected_page, **{'Carte nella ricerca': len(query_seen)})
                        journal.write(json.dumps({'pagina': item, 'righe': updated_rows}, ensure_ascii=False) + '\n')
                        journal.flush()
                        print(f"  Pagina {state['page']}/{expected_pages} | ricerca {len(query_seen)} | totale distinto {len(records)}", flush=True)
                        if state['page'] == expected_pages:
                            break
                        previous = fingerprint(state)
                        page.locator(MORE).wait_for(state='visible', timeout=timeout * 1000)
                        page._buylist_monitor.begin()
                        page.locator(MORE).click()
                        expected_page += 1
                        for attempt in range(3):
                            try:
                                state = wait_ready(page, timeout, previous, expected_page, edition, seen=query_tokens)
                                break
                            except RuntimeError:
                                if attempt == 2:
                                    raise
                                print('Caricamento lento: attendo ancora senza cliccare due volte.', flush=True)
                        if not state['rows']:
                            raise RuntimeError('Pagina vuota prima della fine prevista.')
                    if not query_seen:
                        query_log['Esito'] = 'NESSUN RISULTATO: risposta vuota verificata'
                        print('  Nessun risultato: passo alla prossima espansione.', flush=True)
                    query_log['Stato'] = 'COMPLETATA'
                    journal.write(json.dumps({'ricerca_completata': query_log}, ensure_ascii=False) + '\n')
                    journal.flush()
                    # Il JSONL rimane aggiornato dopo ogni pagina. L'Excel grande
                    # viene riscritto a intervalli, anziché ogni dieci espansioni.
                    if partial_interval and time.monotonic() - last_partial >= partial_interval:
                        save_excel(partial, records, log, False, queries_log=queries_log)
                        last_partial = time.monotonic()
            if len(queries_log) != len(queries) or any(q['Stato'] != 'COMPLETATA' for q in queries_log):
                raise RuntimeError('Non tutte le espansioni sono state completate.')
            final_editions = page.evaluate(READ_STATE)['editions']
            if {q['id'] for q in editions} != {q['id'] for q in final_editions}:
                raise RuntimeError('Elenco espansioni cambiato durante la scansione: ripetere.')
            if not records:
                raise RuntimeError('Nessuna carta estratta.')
            save_excel(output, records, log, True, queries_log=queries_log)
            partial.unlink(missing_ok=True)
            total_variants = sum(len(parse_variants(row)) for row in records.values())
            print(f'ESTRAZIONE COMPLETATA: {len(records)} carte, {total_variants} varianti, {len(log)} pagine, {len(editions)} espansioni. File: {output}', flush=True)
        except Exception as exc:
            if records:
                try:
                    save_excel(partial, records, log, False, str(exc), queries_log)
                    print(f'SALVATO SOLO IL PARZIALE: {partial}', file=sys.stderr, flush=True)
                except Exception as save_error:
                    print(f'Errore salvando Excel parziale: {save_error}. Conservato il giornale JSONL.', file=sys.stderr, flush=True)
            diagnostic = {'version': VERSION, 'url': URL, 'error': str(exc),
                          'cards': len(records), 'queries': queries_log,
                          'timestamp_utc': datetime.now(timezone.utc).isoformat()}
            diagnostic['network'] = {
                'successful_responses': page._buylist_monitor.successful_responses,
                'successful_json': page._buylist_monitor.successful_json,
                'errors': page._buylist_monitor.errors,
            }
            try:
                diagnostic['page_state'] = page.evaluate(READ_STATE)
            except Exception as read_error:
                diagnostic['state_error'] = str(read_error)
            output.with_suffix('.errore.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
            try:
                page.screenshot(path=str(output.with_suffix('.errore.png')), full_page=False)
            except Exception:
                pass
            raise
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=OUTPUT)
    parser.add_argument('--timeout', type=int, default=120, help='Timeout in secondi per ogni pagina')
    parser.add_argument('--headed', action='store_true', help='Mostra il browser durante la lettura locale')
    parser.add_argument('--load-images', action='store_true', help='Scarica anche immagini, font e video (più lento)')
    parser.add_argument('--partial-interval', type=int, default=900,
                        help='Secondi tra copie Excel parziali; 0 = solo su errore (JSONL sempre aggiornato)')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout deve essere positivo')
    if args.partial_interval < 0:
        parser.error('--partial-interval non può essere negativo')
    scrape(args.output, args.timeout, args.headed, args.partial_interval, args.load_images)


if __name__ == '__main__':
    main()
