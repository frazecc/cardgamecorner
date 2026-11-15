import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from supabase_sync import SupabaseClient, prepare_card, batches, load_source, sync
from scraper import parse_variants, price_rows, product_key


class SupabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = Path(__file__).parent / 'fixtures' / 'missing_dimension.json'
        data = json.loads(fixture.read_text())
        cls.row = data if 'product' in data else data['row']

    def test_original_variants_preserved_with_missing_dimensions(self):
        original = copy.deepcopy(self.row)
        result = prepare_card(self.row)
        self.assertEqual([x['raw'] for x in result['variants']], parse_variants(self.row))
        self.assertNotIn('Variants', result['raw']['product'])
        self.assertEqual(self.row, original)
        expected = {p['Chiave voce'] for p in price_rows({product_key(self.row): self.row})}
        self.assertEqual({v['key'] for v in result['variants']}, expected)

    def test_distinct_repeated_ids_are_not_dropped(self):
        row = copy.deepcopy(self.row)
        first = parse_variants(row)[0]
        second = dict(first, BuyList=first['BuyList'] + 1)
        row['product']['Variants'] = json.dumps([first, second, first])
        variants = prepare_card(row)['variants']
        self.assertEqual(len(variants), 2)
        self.assertNotEqual(variants[0]['key'], variants[1]['key'])

    def test_batches_respect_card_limit_and_keep_last(self):
        blocks = list(batches([{'key': str(i)} for i in range(205)], size=100))
        self.assertEqual([len(b) for b in blocks], [100, 100, 5])
        self.assertEqual([r['key'] for b in blocks for r in b], list(map(str, range(205))))

    def test_payload_bytes_split_without_losing_large_single_card(self):
        rows = [{'key': str(i), 'data': 'あ' * 20} for i in range(3)]
        self.assertEqual([r for b in batches(rows, max_bytes=50) for r in b], rows)
        self.assertEqual(len(list(batches(rows, max_bytes=50))), 3)

    def test_rejects_wrong_host_and_publishable_write_key(self):
        for url in ['http://x.supabase.co', 'https://x.supabase.co.evil.com',
                    'https://user:password@x.supabase.co', 'https://x.supabase.co/rest']:
            with self.assertRaises(ValueError):
                SupabaseClient(url, 'sb_secret_example')
        with self.assertRaises(ValueError):
            SupabaseClient('https://x.supabase.co', 'sb_publishable_example')

    def test_secret_key_never_used_as_bearer_jwt(self):
        client = SupabaseClient('https://x.supabase.co', 'sb_secret_example')
        self.assertNotIn('Authorization', client.headers)

    def test_retry_does_not_change_rpc_body(self):
        session = Mock()
        session.post.side_effect = [Mock(ok=False, status_code=503),
                                    Mock(ok=True, content=b'2', json=lambda: 2)]
        client = SupabaseClient('https://x.supabase.co', 'sb_secret_example', session)
        body = {'p_id': 'test', 'p_batch': []}
        with patch('supabase_sync.time.sleep'):
            self.assertEqual(client.rpc('cgc_ingest', body), 2)
        self.assertEqual(session.post.call_args_list[0], session.post.call_args_list[1])

    def test_partial_source_is_not_published(self):
        source = {'records': {'x': self.row}, 'queries': [{'id': 1}, {'id': 2}],
                  'completed': {'1': {}}, 'valid_bytes': 1}
        with patch('supabase_sync.load_checkpoint', return_value=source):
            with self.assertRaisesRegex(ValueError, 'PARZIALE'):
                load_source('unused')

    def test_http_error_does_not_leak_response_or_key(self):
        session = Mock()
        session.post.return_value = Mock(ok=False, status_code=403, text='sb_secret_sensitive')
        client = SupabaseClient('https://x.supabase.co', 'sb_secret_sensitive', session)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403') as ctx:
            client.rpc('cgc_ingest', {})
        self.assertNotIn('sensitive', str(ctx.exception))

    def test_completed_checkpoint_is_verified_without_reimport(self):
        source = {'records': {'x': self.row}, 'queries': [{'id': None}]}
        count = len(parse_variants(self.row))
        client = Mock()
        client.rpc.side_effect = ['COMPLETE', {'state': 'COMPLETE', 'cards': 1, 'prices': count}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.jsonl'
            path.write_text('checkpoint')
            with patch('supabase_sync.load_source', return_value=(source, count)):
                sync(path, client=client)
        self.assertEqual([x.args[0] for x in client.rpc.call_args_list],
                         ['cgc_delta_open', 'cgc_delta_complete'])

    def test_incomplete_ack_never_publishes_new_prices(self):
        source = {'records': {'x': self.row}, 'queries': [{'id': None}]}
        count = len(parse_variants(self.row))
        client = Mock()
        client.rpc.side_effect = ['LOADING', 0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.jsonl'
            path.write_text('checkpoint')
            with patch('supabase_sync.load_source', return_value=(source, count)):
                with self.assertRaisesRegex(RuntimeError, 'confermate'):
                    sync(path, client=client)
        self.assertNotIn('cgc_delta_complete', [x.args[0] for x in client.rpc.call_args_list])


if __name__ == '__main__':
    unittest.main()
