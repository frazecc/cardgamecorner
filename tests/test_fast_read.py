import unittest
from unittest.mock import patch
import scraper


class ProjectionPage:
    def __init__(self):
        self.clock = 0
        self.arguments = []

    def evaluate(self, script, argument=None):
        self.arguments.append(argument)
        keys = ['Id:1', 'Id:2']
        ids = [1, 2] if argument.get('probe') else [2]
        return dict(page=2, pages=3, pending=0, filters={'edition': 2364},
                    no_results=False, only_hot=False, all_keys=keys, editions=[],
                    rows=[dict(product={'Id': i}, name='Carta', image=f'{i}.jpg', expansion='Base') for i in ids])

    def wait_for_timeout(self, milliseconds):
        self.clock += milliseconds / 1000


class FastReadTests(unittest.TestCase):
    def test_only_one_full_read_after_probes_and_excludes_processed_cards(self):
        page = ProjectionPage()
        with patch.object(scraper.time, 'monotonic', side_effect=lambda: page.clock):
            result = scraper.wait_ready(page, 5, expected_page=2, edition=2364, seen={'Id:1'})
        full_reads = [a for a in page.arguments if not a.get('probe')]
        self.assertEqual(len(full_reads), 1)
        self.assertEqual(full_reads[0]['seen'], ['Id:1'])
        self.assertGreaterEqual(len(page.arguments), 5)
        self.assertEqual([r['product']['Id'] for r in result['rows']], [2])
        self.assertEqual(scraper.fingerprint(result), ('Id:1', 'Id:2'))

    def test_snapshot_change_after_probe_is_not_accepted(self):
        page = ProjectionPage()
        original_evaluate = page.evaluate
        changed = [False]

        def evaluate(script, argument=None):
            value = original_evaluate(script, argument)
            if not argument.get('probe') and not changed[0]:
                value['all_keys'] = ['Id:wrong']
                changed[0] = True
            return value

        page.evaluate = evaluate
        with patch.object(scraper.time, 'monotonic', side_effect=lambda: page.clock):
            result = scraper.wait_ready(page, 5, expected_page=2, edition=2364)
        self.assertEqual(result['all_keys'], ['Id:1', 'Id:2'])
        self.assertEqual(len([a for a in page.arguments if not a.get('probe')]), 2)


if __name__ == '__main__':
    unittest.main()
