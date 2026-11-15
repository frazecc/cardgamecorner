import os
import shutil
import subprocess
import unittest
from pathlib import Path
import scraper


class ReadStateJavaScriptTests(unittest.TestCase):
    def test_real_javascript_probe_and_projection(self):
        node = os.environ.get('CODEX_PRIMARY_RUNTIME_NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node non installato: prova JavaScript eseguita su GitHub Actions')
        fixtures = Path(__file__).parent / 'fixtures'
        result = subprocess.run([node, str(fixtures / 'read_state_harness.js'), str(fixtures / 'variants.json')],
                                input=scraper.READ_STATE, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('snapshot completo solo per carte nuove', result.stdout)


if __name__ == '__main__':
    unittest.main()
