import unittest
from unittest.mock import patch
import scraper


def state(**changes):
    value = dict(page=1, pages=1, pending=0, only_hot=False, filters={},
                 no_results=False, editions=[{'id': '2364', 'name': 'Base'}],
                 rows=[{'product': {'Id': 1}, 'name': 'Carta', 'image': '1.jpg', 'expansion': 'Base'}])
    value.update(changes)
    return value


class FakePage:
    def __init__(self, states):
        self.states = states
        self.index = 0
        self.clock = 0

    def evaluate(self, script):
        value = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return value

    def wait_for_timeout(self, milliseconds):
        self.clock += milliseconds / 1000


class WaitTests(unittest.TestCase):
    def wait(self, page, **kwargs):
        with patch.object(scraper.time, 'monotonic', side_effect=lambda: page.clock):
            return scraper.wait_ready(page, 5, **kwargs)

    def test_waits_for_editions_to_load(self):
        page = FakePage([state(editions=[]), state(editions=[]), state()])
        result = self.wait(page, require_editions=True)
        self.assertTrue(result['editions'])
        self.assertGreaterEqual(page.clock, 1.5)

    def test_loading_resets_stability(self):
        page = FakePage([state(), state(), state(pending=1), state()])
        self.wait(page)
        self.assertGreaterEqual(page.clock, 1.75)

    def test_empty_result_is_not_accepted_immediately(self):
        page = FakePage([state(no_results=True, rows=[], pages=0)])
        self.wait(page)
        self.assertGreaterEqual(page.clock, 1)

    def test_identical_cards_in_different_queries_are_allowed(self):
        page = FakePage([state(filters={'edition': '2364'})])
        result = self.wait(page, edition=2364, expected_page=1)
        scraper.validate_state(result, edition=2364)

    def test_repeated_page_is_not_accepted(self):
        current = state()
        page = FakePage([current])
        with self.assertRaises(RuntimeError):
            self.wait(page, previous=scraper.fingerprint(current), expected_page=2)

    def test_wrong_edition_is_not_accepted(self):
        page = FakePage([state(filters={'edition': 3534})])
        with self.assertRaises(RuntimeError):
            self.wait(page, edition=2364)

    def test_filter_validation(self):
        with self.assertRaises(RuntimeError):
            scraper.validate_state(state(filters={'edition': 2364}))
        scraper.validate_state(state(filters={'edition': '2364'}), 2364)


if __name__ == '__main__':
    unittest.main()
