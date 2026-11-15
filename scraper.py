"""Esporta l'intera ricerca Pokémon della buylist, seguendo la paginazione reale.

Dipendenze: pip install playwright pandas openpyxl
Browser:    playwright install chromium
Avvio:      python scraper.py

Il foglio Carte riporta i prezzi della combinazione selezionata dal sito per
ciascuna carta, non un prezzo indistinto per tutte le lingue/condizioni.
Dati_originali conserva tutti i campi dei prodotti restituiti dall'applicazione.
Non aggiunge nulla al carrello e non richiede un account.
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.cardgamecorner.com/it/buylist-2026?game=pokemon"
OUTPUT = "CardGameCorner_Buylist_Pokemon_Completa.xlsx"
VERSION = "3.1.0"
MORE = 'button[ng-click="showMore()"]'
PRODUCT = '[ng-repeat="product in expansion.Products"]'

# Legge gli stessi oggetti usati dai template Angular della pagina. Non inventa
# endpoint o selettori di prezzo e non interpreta div generici contenenti euro.
READ_STATE = r"""() => {
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
    const rows = [...document.querySelectorAll('[ng-repeat="product in expansion.Products"]')].map(el => {
        const child = angular.element(el).scope();
        if (!child || !child.product) throw Error('Prodotto senza dati Angular.');
        const text = selector => (el.querySelector(selector)?.textContent || '').trim();
        const selected = model => {
            const select = el.querySelector('select[ng-model="' + model + '"]');
            return select?.selectedOptions[0]?.textContent?.trim() || '';
        };
        const options = model => [...(el.querySelector('select[ng-model="' + model + '"]')?.options || [])]
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
            languages: options('product.Language'),
            conditions: options('product.Condition'),
            reverse: el.querySelector('input[ng-model="product.Reverse"]')?.checked || false,
            first_edition: el.querySelector('input[ng-model="product.firstEdition"]')?.checked || false,
            evaluation: el.innerText.includes('Riceverai una valutazione'),
        };
    });
    return {
        page: Number(scope.PageNumber), pages: Number(scope.PageCount), pending,
        no_results: document.body.innerText.includes('Nessun prodotto corrisponde alla tua ricerca.'),
        // SearchMobile chiude i filtri e può rimuovere i select dal DOM.
        // L'elenco dei dati resta nello scope anche a pannello chiuso.
        editions: (scope.Filters?.Expansions || []).filter(exp=>exp.Id != null)
            .map(exp=>({id:exp.Id, name:exp.Espansione})),
        only_hot: scope.OnlyBuyList,
        filters: clean(scope.SelectedFilters || {}), rows,
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
    return tuple(product_key(row) for row in state['rows'])


def wait_ready(page, timeout, previous=None, expected_page=None, edition=None, require_editions=False):
    deadline = time.monotonic() + timeout
    stable = None
    since = None
    last = None
    while time.monotonic() < deadline:
        state = page.evaluate(READ_STATE)
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
        if state['no_results'] and not state['rows']:
            marker = ('empty', str(edition), state['page'], state['pages'])
            if marker != stable:
                stable, since = marker, time.monotonic()
            elif time.monotonic() - since >= 1.0:
                return state
            page.wait_for_timeout(250)
            continue
        if state and state['rows'] and state['pending'] == 0 and edition_ok:
            if state['pages'] < 1 or state['page'] < 1:
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
                    return state
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
    if state['no_results'] and not state['rows']:
        return
    if not 1 <= state['page'] <= state['pages']:
        raise RuntimeError('Numeri di pagina non validi.')


def save_excel(path, records, pages_log, complete, error='', queries_log=None):
    queries_log = queries_log or []
    cards, raw = [], []
    for key, row in records.items():
        p = row['product']
        cards.append({
            'ID': key,
            'Carta': row['name'],
            'Nome alternativo': row['name_en'],
            'Espansione': row['expansion'],
            'Prezzo Buylist (testo sito)': row['price_text'],
            'MinAcquisto (dato sito)': p.get('MinAcquisto'),
            'MaxAcquisto (dato sito)': p.get('MaxAcquisto'),
            'Lingua selezionata': row['language'],
            'Condizione selezionata': row['condition'],
            'Reverse selezionato': row['reverse'],
            'Prima edizione selezionata': row['first_edition'],
            'Valutazione su richiesta': row['evaluation'],
            'Lingue disponibili': ', '.join(row['languages']),
            'Condizioni disponibili': ', '.join(row['conditions']),
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
        {'Voce': 'Pagine caricate', 'Valore': len(pages_log)},
        {'Voce': 'Ricerche completate', 'Valore': sum(q['Stato'] == 'COMPLETATA' for q in queries_log)},
        {'Voce': 'Prezzi', 'Valore': 'Combinazione selezionata dal sito per ogni carta. Non tutte le combinazioni lingua/condizione.'},
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
        for name, items in [('Carte', cards), ('Pagine', pages_log), ('Info', info), ('Espansioni', queries_log), ('Dati_originali', raw)]:
            frame(items).to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = min(65, max(14, len(str(column[0].value or '')) + 3))
    temp.replace(path)


def scrape(output, timeout=120, headed=False):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem.replace('_Completa', '') + '_PARZIALE.xlsx')
    checkpoint = output.with_suffix('.checkpoint.jsonl')
    records, log, queries_log = {}, [], []
    print(f'CardGameCorner scraper {VERSION} | {URL}', flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        page.set_default_timeout(timeout * 1000)
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
            page.locator('#button-search').click()
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
                        page.locator('#button-search').click()
                        # Ricerche diverse possono avere risultati identici:
                        # il fingerprint è necessario solo nella paginazione.
                        state = wait_ready(page, timeout, expected_page=1, edition=edition)
                    validate_state(state, edition)
                    query_seen = set()
                    expected_page = 1
                    expected_pages = state['pages']
                    while state['rows']:
                        validate_state(state, edition)
                        if state['page'] != expected_page or state['pages'] != expected_pages:
                            raise RuntimeError('Paginazione cambiata o non consecutiva. Risultato parziale.')
                        new_rows, new_local = [], 0
                        for row in state['rows']:
                            if not row['name']:
                                raise RuntimeError('Template di una carta non ancora caricato.')
                            key = product_key(row)
                            old = records.get(key)
                            if old and (old['image'], old['name']) != (row['image'], row['name']):
                                raise RuntimeError(f'Identificatore ambiguo: {key}. Nessuna fusione automatica.')
                            if key not in query_seen:
                                new_local += 1
                                query_seen.add(key)
                            if key not in records:
                                new_rows.append(row)
                            records[key] = row
                        if new_local == 0:
                            raise RuntimeError('Pagina senza carte nuove nella ricerca corrente.')
                        item = {'Espansione ricercata': query['name'], 'Pagina': state['page'],
                                'Pagine totali': expected_pages, 'Nuove carte': len(new_rows),
                                'Carte nella ricerca': len(query_seen), 'Carte distinte cumulative': len(records),
                                'Contatori sito': json.dumps(state['counters'], ensure_ascii=False)}
                        log.append(item)
                        query_log.update(Pagine=expected_page, **{'Carte nella ricerca': len(query_seen)})
                        journal.write(json.dumps({'pagina': item, 'righe': new_rows}, ensure_ascii=False) + '\n')
                        journal.flush()
                        print(f"  Pagina {state['page']}/{expected_pages} | ricerca {len(query_seen)} | totale distinto {len(records)}", flush=True)
                        if state['page'] == expected_pages:
                            break
                        previous = fingerprint(state)
                        page.locator(MORE).wait_for(state='visible', timeout=timeout * 1000)
                        page.locator(MORE).click()
                        expected_page += 1
                        for attempt in range(3):
                            try:
                                state = wait_ready(page, timeout, previous, expected_page, edition)
                                break
                            except RuntimeError:
                                if attempt == 2:
                                    raise
                                print('Caricamento lento: attendo ancora senza cliccare due volte.', flush=True)
                        if not state['rows']:
                            raise RuntimeError('Pagina vuota prima della fine prevista.')
                    query_log['Stato'] = 'COMPLETATA'
                    journal.write(json.dumps({'ricerca_completata': query_log}, ensure_ascii=False) + '\n')
                    journal.flush()
                    # Il giornale viene aggiornato dopo ogni pagina; ogni dieci
                    # ricerche si aggiorna anche la copia Excel parziale.
                    if (query_index + 1) % 10 == 0:
                        save_excel(partial, records, log, False, queries_log=queries_log)
            if len(queries_log) != len(queries) or any(q['Stato'] != 'COMPLETATA' for q in queries_log):
                raise RuntimeError('Non tutte le espansioni sono state completate.')
            final_editions = page.evaluate(READ_STATE)['editions']
            if {q['id'] for q in editions} != {q['id'] for q in final_editions}:
                raise RuntimeError('Elenco espansioni cambiato durante la scansione: ripetere.')
            if not records:
                raise RuntimeError('Nessuna carta estratta.')
            save_excel(output, records, log, True, queries_log=queries_log)
            partial.unlink(missing_ok=True)
            print(f'ESTRAZIONE COMPLETATA: {len(records)} carte, {len(log)} pagine, {len(editions)} espansioni. File: {output}', flush=True)
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
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout deve essere positivo')
    scrape(args.output, args.timeout, args.headed)


if __name__ == '__main__':
    main()
