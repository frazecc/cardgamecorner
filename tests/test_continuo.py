import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import continuo
import scraper
from supabase_sync import prepare_card

EDITIONS = [{'id': 7, 'name': 'Base'}, {'id': 8, 'name': 'Jungle'}]


def make_row(number):
    rows = json.loads((Path(__file__).parent / 'fixtures/variants.json').read_text())
    row = copy.deepcopy(rows[0])
    row['product']['Id'] = f'CARD{number}'
    row['name'] = f'Carta {number}'
    row['image'] = f'https://example.invalid/{number}.jpg'
    return row


def make_state(rows, page, pages, edition=7):
    return {'only_hot': False, 'filters': {} if edition is None else {'edition': edition}, 'rows': rows,
            'page': page, 'pages': pages, 'no_results': False, 'empty_confirmed': False, 'counters': {},
            'all_keys': [scraper.product_key(r) for r in rows], 'editions': EDITIONS}


class FakeBrowserSite:
    """Simula Site senza browser: serve a verificare scan_query."""
    def __init__(self):
        self.page = MagicMock()
        self.edition_selector = MagicMock()
        self.state = make_state([make_row(99)], 1, 1, edition=None)
        self.opened = 0

    def open(self):
        self.opened += 1
        return EDITIONS


class ScanQueryTests(unittest.TestCase):
    def test_saves_every_page_immediately_and_reports_counts(self):
        site = FakeBrowserSite()
        saved = []
        states = [make_state([make_row(1), make_row(2)], 1, 2), make_state([make_row(3)], 2, 2)]
        with patch('continuo.start_search'), patch('continuo.wait_ready', side_effect=states):
            cards, pages = continuo.scan_query(site, EDITIONS[0], lambda n, c: saved.append((n, c)),
                                               lambda: None, 5)
        self.assertEqual((cards, pages), (3, 2))
        self.assertEqual([n for n, _ in saved], [1, 2])
        self.assertEqual([[c['key'] for c in block] for _, block in saved],
                         [['Id:CARD1', 'Id:CARD2'], ['Id:CARD3']])
        # Ogni carta salvata contiene tutte le varianti del sito.
        expected = len(scraper.parse_variants(make_row(1)))
        self.assertEqual(len(saved[0][1][0]['variants']), expected)
        self.assertEqual(site.opened, 0)

    def test_general_search_starts_from_a_clean_session(self):
        site = FakeBrowserSite()
        saved = []
        with patch('continuo.wait_ready') as wait:
            cards, pages = continuo.scan_query(site, continuo.GENERAL, lambda n, c: saved.append(c),
                                               lambda: None, 5)
        self.assertEqual(site.opened, 1)
        wait.assert_not_called()
        self.assertEqual((cards, pages), (1, 1))
        self.assertEqual(saved[0][0]['key'], 'Id:CARD99')

    def test_non_consecutive_pages_stop_the_search_but_keep_saved_pages(self):
        site = FakeBrowserSite()
        saved = []
        states = [make_state([make_row(1)], 1, 3), make_state([make_row(2)], 3, 3)]
        with patch('continuo.start_search'), patch('continuo.wait_ready', side_effect=states):
            with self.assertRaises(RuntimeError):
                continuo.scan_query(site, EDITIONS[0], lambda n, c: saved.append(n), lambda: None, 5)
        self.assertEqual(saved, [1])

    def test_page_without_new_cards_is_rejected(self):
        site = FakeBrowserSite()
        states = [make_state([make_row(1)], 1, 2), make_state([make_row(1)], 2, 2)]
        with patch('continuo.start_search'), patch('continuo.wait_ready', side_effect=states):
            with self.assertRaises(RuntimeError):
                continuo.scan_query(site, EDITIONS[0], lambda n, c: None, lambda: None, 5)

    def test_verified_empty_search_completes_with_zero_cards(self):
        site = FakeBrowserSite()
        empty = make_state([], 1, 1)
        empty['no_results'] = True
        with patch('continuo.start_search'), patch('continuo.wait_ready', return_value=empty):
            self.assertEqual(continuo.scan_query(site, EDITIONS[0], lambda n, c: None, lambda: None, 5), (0, 0))

    def test_time_check_runs_between_pages(self):
        site = FakeBrowserSite()
        states = [make_state([make_row(1)], 1, 2)]

        def too_late():
            raise TimeoutError('tempo')

        with patch('continuo.start_search'), patch('continuo.wait_ready', side_effect=states):
            with self.assertRaises(TimeoutError):
                continuo.scan_query(site, EDITIONS[0], lambda n, c: None, too_late, 5)

    def test_same_card_twice_in_one_page_merges_variants(self):
        site = FakeBrowserSite()
        first, second = make_row(1), make_row(1)
        variants = scraper.parse_variants(first)
        first['product']['Variants'] = json.dumps(variants[:3])
        second['product']['Variants'] = json.dumps(variants[3:])
        saved = []
        with patch('continuo.start_search'), patch('continuo.wait_ready',
                                                   return_value=make_state([first, second], 1, 1)):
            cards, _ = continuo.scan_query(site, EDITIONS[0], lambda n, c: saved.extend(c), lambda: None, 5)
        self.assertEqual(cards, 1)
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(saved[0]['variants']), len(variants))


class FakeClient:
    def __init__(self, claims):
        self.claims = list(claims)
        self.calls = []

    def rpc(self, name, body):
        self.calls.append((name, body))
        if name == 'cgc_live_register':
            return len(body['p_queries'])
        if name == 'cgc_live_claim':
            return self.claims.pop(0) if self.claims else None
        if name == 'cgc_live_clean':
            return {'state': 'IN ATTESA'}
        if name == 'cgc_live_fail':
            return 1
        return {}

    def names(self):
        return [name for name, _ in self.calls]


class FakeSite:
    def __init__(self):
        self.opened = 0

    def open(self):
        self.opened += 1
        return EDITIONS


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def claim(key, name):
    return {'key': key, 'name': name}


class WorkTests(unittest.TestCase):
    def run_work(self, client, scan, clock=None, **kwargs):
        clock = clock or Clock()
        site = FakeSite()
        kwargs.setdefault('max_runtime', 1000)
        with patch('builtins.print'):
            done = continuo.work(client, site, 'run1', clock=clock, sleep=clock.sleep, scan=scan, **kwargs)
        return done, site

    def test_registers_claims_saves_finishes_and_cleans(self):
        client = FakeClient([claim('7', 'Base')])
        card = prepare_card(make_row(1))

        def scan(site, query, save, check, timeout):
            self.assertEqual(query['name'], 'Base')
            save(1, [card])
            return 1, 1

        done, _ = self.run_work(client, scan, max_queries=1)
        self.assertEqual(done, 1)
        self.assertEqual(client.names(), ['cgc_live_register', 'cgc_live_claim', 'cgc_live_save_page',
                                          'cgc_live_finish', 'cgc_live_clean'])
        registered = client.calls[0][1]['p_queries']
        self.assertEqual([q['key'] for q in registered], ['__generale__', '7', '8'])
        saved = client.calls[2][1]
        self.assertEqual((saved['p_run'], saved['p_query'], saved['p_page']), ('run1', '7', 1))
        self.assertEqual(saved['p_batch'][0]['key'], 'Id:CARD1')
        finished = client.calls[3][1]
        self.assertEqual((finished['p_cards'], finished['p_pages']), (1, 1))

    def test_failure_is_reported_and_the_next_search_continues(self):
        client = FakeClient([claim('7', 'Base'), claim('8', 'Jungle')])
        calls = []

        def scan(site, query, save, check, timeout):
            calls.append(query['name'])
            if query['name'] == 'Base':
                raise RuntimeError('Paginazione cambiata')
            return 2, 1

        done, site = self.run_work(client, scan, max_queries=1)
        self.assertEqual(calls, ['Base', 'Jungle'])
        self.assertEqual(done, 1)
        fail = next(body for name, body in client.calls if name == 'cgc_live_fail')
        self.assertEqual(fail['p_query'], '7')
        self.assertIn('Paginazione cambiata', fail['p_error'])
        self.assertEqual(site.opened, 2)  # sessione riaperta dopo l'errore

    def test_stops_after_too_many_consecutive_failures(self):
        client = FakeClient([claim('7', 'Base')] * 10)

        def scan(site, query, save, check, timeout):
            raise RuntimeError('sito non raggiungibile')

        with self.assertRaises(RuntimeError):
            self.run_work(client, scan)
        self.assertEqual(client.names().count('cgc_live_fail'), continuo.MAX_CONSECUTIVE_FAILURES)

    def test_no_new_search_after_the_time_limit(self):
        clock = Clock()
        client = FakeClient([claim('7', 'Base'), claim('8', 'Jungle'), claim('__generale__', 'Ricerca generale')])

        def scan(site, query, save, check, timeout):
            clock.now += 60
            return 1, 1

        done, _ = self.run_work(client, scan, clock=clock, max_runtime=100, pause=0)
        self.assertEqual(done, 2)
        self.assertEqual(client.names().count('cgc_live_claim'), 2)

    def test_hard_deadline_interrupts_a_running_search(self):
        clock = Clock()
        client = FakeClient([claim('7', 'Base')])

        def scan(site, query, save, check, timeout):
            clock.now += 5000
            check()
            return 1, 1

        done, _ = self.run_work(client, scan, clock=clock, max_runtime=100, grace=10, pause=0)
        # La ricerca interrotta e liberata e segnalata; il run poi termina normalmente.
        self.assertEqual(done, 0)
        fail = next(body for name, body in client.calls if name == 'cgc_live_fail')
        self.assertIn('TimeoutError', fail['p_error'])
        self.assertNotIn('cgc_live_finish', client.names())

    def test_search_missing_from_the_site_is_reported_not_scanned(self):
        client = FakeClient([claim('999', 'Sparita'), claim('7', 'Base')])
        scanned = []

        def scan(site, query, save, check, timeout):
            scanned.append(query['name'])
            return 1, 1

        self.run_work(client, scan, max_queries=1)
        self.assertEqual(scanned, ['Base'])
        fail = next(body for name, body in client.calls if name == 'cgc_live_fail')
        self.assertEqual(fail['p_query'], '999')

    def test_waits_when_no_search_is_available(self):
        clock = Clock()
        client = FakeClient([None, claim('7', 'Base')])
        client.claims = [None, claim('7', 'Base')]

        def scan(site, query, save, check, timeout):
            return 1, 1

        done, _ = self.run_work(client, scan, clock=clock, max_queries=1)
        self.assertEqual(done, 1)
        self.assertGreaterEqual(clock.now, 30)

    def test_clean_failure_does_not_undo_a_completed_search(self):
        client = FakeClient([claim('7', 'Base')])
        original = client.rpc

        def rpc(name, body):
            if name == 'cgc_live_clean':
                raise RuntimeError('Supabase HTTP 500')
            return original(name, body)

        client.rpc = rpc
        done, _ = self.run_work(client, lambda *a: (1, 1), max_queries=1)
        self.assertEqual(done, 1)



class EmptyResponseProtectionTests(unittest.TestCase):
    """Il 25/11/2026 il sito ha risposto "vuoto" a centinaia di ricerche e la pulizia ha tolto le carte."""

    run_work = WorkTests.run_work

    def test_empty_search_is_rechecked_with_a_fresh_session(self):
        client = FakeClient([claim('7', 'Base')])
        card = prepare_card(make_row(1))
        answers = [(0, 0), (1, 1)]

        def scan(site, query, save, check, timeout):
            cards, pages = answers.pop(0)
            if cards:
                save(1, [card])
            return cards, pages

        done, site = self.run_work(client, scan, max_queries=1)
        self.assertEqual(done, 1)
        self.assertEqual(site.opened, 2)  # apertura iniziale + sessione nuova per il ricontrollo
        finished = [body for name, body in client.calls if name == 'cgc_live_finish'][0]
        self.assertEqual(finished['p_cards'], 1)

    def test_confirmed_empty_search_completes_when_the_control_search_works(self):
        client = FakeClient([claim('7', 'Base')])
        scanned = []

        def scan(site, query, save, check, timeout):
            scanned.append(query['name'])
            return (3, 1) if query['name'] == 'Jungle' else (0, 0)

        done, _ = self.run_work(client, scan, max_queries=1)
        self.assertEqual(done, 1)
        self.assertEqual(scanned, ['Base', 'Base', 'Jungle'])
        finished = [body for name, body in client.calls if name == 'cgc_live_finish'][0]
        self.assertEqual(finished['p_cards'], 0)
        self.assertNotIn('cgc_live_save_page', client.names())

    def test_silent_site_stops_without_marking_the_search_complete(self):
        client = FakeClient([claim('7', 'Base'), claim('8', 'Jungle')])
        with self.assertRaises(continuo.SiteSilent):
            self.run_work(client, lambda *a: (0, 0), max_queries=2)
        self.assertIn('cgc_live_fail', client.names())
        self.assertNotIn('cgc_live_finish', client.names())
        self.assertNotIn('cgc_live_clean', client.names())

    def test_removals_are_applied_and_a_large_one_is_reported_at_the_end(self):
        client = FakeClient([claim('7', 'Base'), claim('8', 'Jungle'), claim('7', 'Base')])
        original = client.rpc

        def rpc(name, body):
            if name == 'cgc_live_clean':
                client.calls.append((name, body))
                return {'state': 'ESEGUITA', 'rimosse': 300}
            return original(name, body)

        client.rpc = rpc
        with self.assertRaises(continuo.TooManyRemoved):
            self.run_work(client, lambda *a: (1, 1), max_queries=3, max_removed=500)
        self.assertEqual(client.names().count('cgc_live_finish'), 3)   # il lavoro viene completato comunque

    def test_browser_session_is_renewed_periodically(self):
        client = FakeClient([claim('7', 'Base'), claim('8', 'Jungle'), claim('7', 'Base')])
        clock = Clock()

        def scan(site, query, save, check, timeout):
            clock.now += 1000
            return 1, 1

        done, site = self.run_work(client, scan, clock=clock, max_queries=3, max_runtime=10000, refresh=1500)
        self.assertEqual(done, 3)
        self.assertEqual(site.opened, 2)  # apertura iniziale + un rinnovo prima della terza ricerca


if __name__ == '__main__':
    unittest.main()
