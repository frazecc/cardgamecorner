"""Esporta l'intera ricerca PokÃ©mon della buylist, seguendo la paginazione reale.

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


def wait_ready(page, timeout, previous=None, expected_page=None):
    deadline = time.monotonic() + timeout
    stable = None
    since = None
    last = None
    while time.monotonic() < deadline:
        state = page.evaluate(READ_STATE)
        if state and state['rows'] and state['pending'] == 0:
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


def validate_state(state):
    if state['only_hot'] is not False:
        raise RuntimeError('Il filtro Hot Cards non Ã¨ impostato su Tutto.')
    active = {k: v for k, v in state['filters'].items() if v not in (None, '', False, [])}
    if active:
        raise RuntimeError(f'Ricerca ancora filtrata: {active}')
    if not 1 <= state['page'] <= state['pages']:
        raise RuntimeError('Numeri di pagina non validi.')


def save_excel(path, records, pages_log, complete, error=''):
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
        {'Voce': 'Stato', 'Valore': 'COMPLETO: raggiunta ultima pagina' if complete else 'PARZIALE'},
        {'Voce': 'Fonte', 'Valore': URL},
        {'Voce': 'Data UTC', 'Valore': datetime.now(timezone.utc).isoformat()},
        {'Voce': 'Carte distinte', 'Valore': len(cards)},
        {'Voce': 'Pagine caricate', 'Valore': len(pages_log)},
        {'Voce': 'Pagine dichiarate dal sito', 'Valore': pages_log[-1]['Pagine totali'] if pages_log else ''},
        {'Voce': 'Prezzi', 'Valore': 'Combinazione selezionata dal sito per ogni carta. Non tutte le combinazioni lingua/condizione.'},
        {'Voce': 'Copertura', 'Valore': 'Ricerca PokÃ©mon senza filtri; incluse carte non acquistate o con valutazione su richiesta se restituite dal sito.'},
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
        for name, items in [('Carte', cards), ('Pagine', pages_log), ('Info', info), ('Dati_originali', raw)]:
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
    records, log = {}, []
    last_state = None
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
            # Attendere l'inizializzazione: la home iniziale NON Ã¨ il catalogo.
            page.wait_for_function("""() => {
                const el = document.querySelector('#simplesearch');
                if (!el || !window.angular) return false;
                const s = angular.element(el).scope();
                return s && Array.isArray(s.BuylistSmallProducts) && s.BuylistSmallProducts.length > 0;
            }""", timeout=timeout * 1000)
            page.locator('#simplesearch').fill('')
            page.locator('select[ng-model="OnlyBuyList"]').select_option(label='Tutto')
            # La pagina Ã¨ appena aperta: tutti gli altri filtri sono vuoti.
            page.locator('#button-search').click()
            last_state = wait_ready(page, timeout, expected_page=1)
            validate_state(last_state)
            with checkpoint.open('w', encoding='utf-8') as journal:
                while True:
                    state = last_state
                    validate_state(state)
                    if state['page'] != len(log) + 1:
                        raise RuntimeError('Paginazione non consecutiva: estrazione interrotta.')
                    if log and state['pages'] != log[0]['Pagine totali']:
                        raise RuntimeError('Numero di pagine cambiato durante la lettura. Ripetere per ottenere una scansione coerente.')
                    before = len(records)
                    for row in state['rows']:
                        if not row['name']:
                            raise RuntimeError('Template di una carta non ancora caricato.')
                        records[product_key(row)] = row
                    added = len(records) - before
                    if added == 0:
                        raise RuntimeError('Pagina senza nuove carte: possibile risposta duplicata, non dichiarata completa.')
                    item = {'Pagina': state['page'], 'Pagine totali': state['pages'],
                            'Nuove carte': added, 'Carte distinte cumulative': len(records),
                            'Contatori sito': json.dumps(state['counters'], ensure_ascii=False)}
                    log.append(item)
                    journal.write(json.dumps({'pagina': item, 'righe': state['rows']}, ensure_ascii=False) + '\n')
                    journal.flush()
                    print(f"Pagina {state['page']}/{state['pages']} | +{added} carte | totale {len(records)}", flush=True)
                    if state['page'] == state['pages']:
                        break
                    more = page.locator(MORE)
                    more.wait_for(state='visible', timeout=timeout * 1000)
                    previous = fingerprint(state)
                    # Nessun limite a 45/60 carte, nessuno scroll e nessun tetto arbitrario di pagine.
                    more.click()
                    last_state = wait_ready(page, timeout, previous, state['page'] + 1)
            save_excel(output, records, log, complete=True)
            print(f'ESTRAZIONE COMPLETATA: {len(records)} carte, {len(log)} pagine. File: {output}', flush=True)
        except Exception as exc:
            if records:
                save_excel(partial, records, log, complete=False, error=str(exc))
                print(f'SALVATO SOLO IL PARZIALE: {partial}', file=sys.stderr, flush=True)
            page.screenshot(path=str(output.with_suffix('.errore.png')), full_page=False)
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
