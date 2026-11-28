"""Scanner continuo CardGameCorner -> Supabase.

Ripete in ciclo: chiede a Supabase la ricerca (espansione) controllata da piu
tempo, la legge pagina per pagina e SALVA SUBITO ogni carta con la sua data di
controllo. Dopo il tempo massimo si ferma da solo, cosi il workflow GitHub puo
riavviarlo prima del limite delle 6 ore. Non modifica scraper.py: ne riusa le
funzioni di lettura gia collaudate.

Le chiavi segrete si leggono soltanto dall'ambiente.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

from scraper import (MORE, URL, SearchMonitor, fingerprint, merge_product_row, product_key,
                     query_key, start_search, validate_state, wait_ready)
from supabase_sync import DEFAULT_URL, SupabaseClient, batches, prepare_card

GENERAL = {'id': None, 'name': 'Ricerca generale'}
MAX_CONSECUTIVE_FAILURES = 6
# Protezioni contro un sito che smette di rispondere (visto il 25/11/2026: centinaia di ricerche
# "vuote" e catalogo svuotato dalla pulizia): sessione rinnovata ogni 30 minuti, ricerche vuote
# ricontrollate con una sessione nuova e con una ricerca di controllo; le rimozioni confermate si applicano
# sempre, ma se sono molte il run finisce con un avviso (email di GitHub).
REFRESH_SECONDS = 1800
MAX_REMOVED_PER_RUN = 500
CANARY_NAMES = ('Southern Islands', 'Jungle', 'Base', 'Fossil')


class SiteSilent(RuntimeError):
    """Il sito non restituisce piu carte: meglio fermarsi che segnare ricerche vuote."""


class TooManyRemoved(RuntimeError):
    """In un solo run sono state tolte molte carte: avviso a fine run, i dati sono gia aggiornati."""

# Stesso criterio di scraper.py: Angular puo codificare lo stesso ID come
# number:123, string:123 o valore semplice. Si usa il valore reale dell'opzione.
OPTION_VALUE_JS = """(el, id) => {
    const matches = [...el.options].filter(o => {
        const value = o.value.replace(/^(number|string):/, '');
        return value === String(id);
    });
    if (matches.length !== 1) throw Error('Espansione non selezionabile: ' + id);
    return matches[0].value;
}"""


class Site:
    """Sessione del browser sul sito, riapribile dopo un errore."""

    def __init__(self, playwright, timeout, headed=False, load_images=False):
        self.playwright = playwright
        self.timeout = timeout
        self.headed = headed
        self.load_images = load_images
        self.browser = None
        self.page = None
        self.state = None
        self.editions = []
        self.edition_selector = None

    def open(self):
        """(Ri)apre il sito, esegue la ricerca generale e legge l'elenco espansioni."""
        self.close_page()
        if self.browser is None:
            self.browser = self.playwright.chromium.launch(headless=not self.headed)
        timeout = self.timeout
        page = self.browser.new_page(viewport={'width': 1920, 'height': 1080})
        if not self.load_images:
            page.route('**/*', lambda route: route.abort() if route.request.resource_type
                       in ('image', 'media', 'font') else route.continue_())
        page.set_default_timeout(timeout * 1000)
        page._buylist_monitor = SearchMonitor(page)
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
        self.page = page
        self.state = state
        self.editions = editions
        self.edition_selector = page.locator('select[ng-options*="Filters.Expansions"]')
        return editions

    def close_page(self):
        if self.page is not None:
            try:
                self.page.close()
            except Exception:
                pass
            self.page = None

    def close(self):
        self.close_page()
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None


def scan_query(site, query, on_page, check_time, timeout):
    """Legge tutte le pagine di una ricerca; ogni pagina e salvata subito con on_page.

    Restituisce (carte, pagine). Qualsiasi anomalia di paginazione interrompe la
    ricerca con un errore: le pagine gia salvate restano valide.
    """
    edition = query['id']
    if edition is None:
        # La ricerca generale richiede filtri vuoti: si riparte da una sessione pulita.
        site.open()
        state = site.state
    else:
        page, selector = site.page, site.edition_selector
        # La ricerca chiude i filtri: riaprirli quando serve.
        if not selector.is_visible():
            page.locator('#btn-filters').click()
        selector.wait_for(state='visible', timeout=timeout * 1000)
        option_value = selector.evaluate(OPTION_VALUE_JS, edition)
        selector.select_option(value=option_value)
        start_search(page)
        state = wait_ready(page, timeout, expected_page=1, edition=edition)
    page = site.page
    validate_state(state, edition)
    query_rows, query_seen, query_tokens = {}, set(), set()
    expected_page, expected_pages = 1, state['pages']
    while state['rows']:
        validate_state(state, edition)
        if state['page'] != expected_page or state['pages'] != expected_pages:
            raise RuntimeError('Paginazione cambiata o non consecutiva. Risultato parziale.')
        touched, new_local = {}, 0
        for row in state['rows']:
            if not row['name']:
                raise RuntimeError('Template di una carta non ancora caricato.')
            key = product_key(row)
            if key not in query_seen:
                new_local += 1
                query_seen.add(key)
            merged = merge_product_row(query_rows.get(key), row)
            query_rows[key] = merged
            touched[key] = merged
        if new_local == 0:
            raise RuntimeError('Pagina senza carte nuove nella ricerca corrente.')
        on_page(state['page'], [prepare_card(row) for row in touched.values()])
        query_tokens.update(state.get('all_keys', ()))
        print(f"  Pagina {state['page']}/{expected_pages} | carte nella ricerca {len(query_seen)}", flush=True)
        if state['page'] == expected_pages:
            break
        check_time()
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
        print('  Nessun risultato: risposta vuota verificata.', flush=True)
    return len(query_seen), (expected_page if query_seen else 0)


def work(client, site, run_id, max_runtime, grace=900, max_queries=None, pause=3.0, timeout=120,
         clock=time.monotonic, sleep=time.sleep, scan=scan_query, refresh=REFRESH_SECONDS,
         max_removed=MAX_REMOVED_PER_RUN):
    """Ciclo principale. Restituisce il numero di ricerche completate."""
    editions = site.open()
    queries = {query_key(q['id']): q for q in [GENERAL] + editions}
    registered = client.rpc('cgc_live_register', {'p_queries': [
        {'key': key, 'name': q['name']} for key, q in queries.items()]})
    print(f'Scanner {run_id}: {registered} ricerche attive, tempo massimo {int(max_runtime)} s.', flush=True)
    start = clock()
    soft_deadline = start + max_runtime
    hard_deadline = soft_deadline + grace
    completed = failures = removed_total = 0
    session_started = start
    good = {}  # ricerche che in questo run hanno restituito carte: chiave -> numero di carte

    def check_time():
        if clock() > hard_deadline:
            raise TimeoutError('Tempo massimo del run raggiunto: ricerca interrotta, i dati gia salvati restano.')

    def reopen():
        nonlocal editions, queries, session_started
        editions = site.open()
        queries = {query_key(q['id']): q for q in [GENERAL] + editions}
        session_started = clock()

    def canary():
        """Ricerca piccola che di solito ha carte, per capire se il sito risponde davvero."""
        if good:
            return queries.get(min(good, key=lambda k: good[k]))
        for wanted in CANARY_NAMES:
            for edition in editions:
                if edition['name'] == wanted:
                    return queries.get(query_key(edition['id']))
        return None

    while clock() < soft_deadline and (max_queries is None or completed < max_queries):
        if refresh and clock() - session_started >= refresh:
            try:
                reopen()
                print('Sessione del browser rinnovata.', flush=True)
            except Exception as error:
                print(f'Rinnovo della sessione non riuscito (continuo con quella attuale): {error}', flush=True)
        claim = client.rpc('cgc_live_claim', {'p_run': run_id})
        if not claim:
            print('Nessuna ricerca disponibile (tutte in lavorazione): attendo.', flush=True)
            sleep(30)
            continue
        key, name = claim['key'], claim['name']
        print(f'Ricerca assegnata: {name}', flush=True)
        started = datetime.now(timezone.utc)

        def save(page_number, cards, key=key):
            for block in batches(cards, size=50):
                client.rpc('cgc_live_save_page', {'p_run': run_id, 'p_query': key,
                                                  'p_page': page_number, 'p_batch': block})

        removed = 0
        try:
            query = queries.get(key)
            if query is None:
                raise RuntimeError(f'Ricerca non piu presente sul sito: {name}')
            cards, pages = scan(site, query, save, check_time, timeout)
            if cards == 0:
                # Una risposta vuota non basta: una sessione "stanca" del browser puo non ricevere dati.
                if query['id'] is not None:
                    print('  Risposta vuota: la ricontrollo con una sessione nuova.', flush=True)
                    reopen()
                    query = queries.get(key)
                    if query is None:
                        raise RuntimeError(f'Ricerca non piu presente sul sito: {name}')
                    cards, pages = scan(site, query, save, check_time, timeout)
                if cards == 0:
                    probe = canary()
                    if probe is not None and probe['id'] != query['id']:
                        found, _ = scan(site, probe, lambda page_number, rows: None, check_time, timeout)
                        if found == 0:
                            raise SiteSilent(
                                f"Il sito non restituisce carte nemmeno per '{probe['name']}': "
                                f"la ricerca '{name}' non viene segnata come vuota e lo scanner si ferma.")
            if cards and query['id'] is not None:
                good[key] = cards
            client.rpc('cgc_live_finish', {'p_run': run_id, 'p_query': key, 'p_started': started.isoformat(),
                                           'p_cards': cards, 'p_pages': pages})
            completed += 1
            failures = 0
            print(f'  Completata: {cards} carte in {pages} pagine.', flush=True)
            try:
                cleaned = client.rpc('cgc_live_clean', {'p_force': False})
                print(f'  Pulizia: {cleaned}', flush=True)
                if isinstance(cleaned, dict) and isinstance(cleaned.get('rimosse'), int):
                    removed = cleaned['rimosse']
            except Exception as error:
                print(f'  Pulizia non eseguita (si riprova al prossimo giro): {error}', flush=True)
        except SiteSilent as error:
            message = f'{type(error).__name__}: {error}'
            print(f'  ERRORE nella ricerca {name}: {message}', flush=True)
            try:
                client.rpc('cgc_live_fail', {'p_run': run_id, 'p_query': key, 'p_error': message})
            except Exception as report_error:
                print(f'  Errore non registrato su Supabase: {report_error}', flush=True)
            raise
        except Exception as error:
            failures += 1
            message = f'{type(error).__name__}: {error}'
            print(f'  ERRORE nella ricerca {name}: {message}', flush=True)
            try:
                client.rpc('cgc_live_fail', {'p_run': run_id, 'p_query': key, 'p_error': message})
            except Exception as report_error:
                print(f'  Errore non registrato su Supabase: {report_error}', flush=True)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(f'{failures} errori consecutivi: scanner fermato.') from error
            sleep(min(30 * failures, 120))
            # Sessione nuova dopo un errore: lo stato della pagina non e piu affidabile.
            reopen()
        removed_total += removed
        sleep(pause)
    print(f'Fine del run: {completed} ricerche completate.', flush=True)
    if max_removed and removed_total > max_removed:
        # Il lavoro e gia stato fatto (il database rispecchia il sito): il run termina "rosso" solo per avvisare,
        # perche GitHub scrive una email quando un workflow fallisce.
        raise TooManyRemoved(f'AVVISO: in questo run sono state tolte {removed_total} carte (soglia {max_removed}). '
                             'I dati sono stati aggiornati come richiesto: controlla che il sito sorgente abbia davvero '
                             'rimosso queste carte.')
    return completed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-runtime', type=int, default=19000,
                        help='Secondi dopo cui non si iniziano nuove ricerche (default 19000, circa 5h15)')
    parser.add_argument('--grace', type=int, default=900,
                        help='Secondi extra per chiudere la ricerca in corso (default 900)')
    parser.add_argument('--max-ricerche', type=int, help='Si ferma dopo N ricerche completate (prova)')
    parser.add_argument('--timeout', type=int, default=120, help='Timeout in secondi per ogni pagina')
    parser.add_argument('--max-rimosse', type=int, default=MAX_REMOVED_PER_RUN,
                        help='Si ferma se in un run vengono tolte piu di N carte (0 = nessun limite)')
    parser.add_argument('--pausa', type=float, default=3.0, help='Secondi di pausa tra una ricerca e la successiva')
    parser.add_argument('--run-id', default=os.environ.get('GITHUB_RUN_ID') or datetime.now(timezone.utc).strftime('locale-%Y%m%d%H%M%S'))
    parser.add_argument('--headed', action='store_true', help='Mostra il browser (solo prove locali)')
    parser.add_argument('--load-images', action='store_true')
    args = parser.parse_args()
    if args.max_runtime < 60 or args.grace < 0 or args.timeout < 1 or args.pausa < 0:
        parser.error('Valori non validi')
    if args.max_ricerche is not None and args.max_ricerche < 1:
        parser.error('--max-ricerche deve essere almeno 1')
    from playwright.sync_api import sync_playwright
    client = SupabaseClient(os.environ.get('SUPABASE_URL') or DEFAULT_URL,
                            os.environ.get('SUPABASE_SERVICE_ROLE_KEY', ''))
    with sync_playwright() as playwright:
        site = Site(playwright, args.timeout, args.headed, args.load_images)
        try:
            work(client, site, args.run_id, args.max_runtime, args.grace, args.max_ricerche,
                 args.pausa, args.timeout, max_removed=args.max_rimosse)
        finally:
            site.close()


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(f'SCANNER FERMATO: {error}', file=sys.stderr, flush=True)
        sys.exit(1)
