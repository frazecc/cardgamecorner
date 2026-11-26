import csv
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.dom import minidom

import openpyxl

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which('node'), 'Node.js non disponibile')
class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        subprocess.run(['node', str(ROOT / 'tests/js/genera_esportazioni.js'), str(cls.out)], check=True,
                       capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def read_csv(self, name, delimiter=';'):
        with (self.out / name).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.reader(stream, delimiter=delimiter))

    def test_csv_for_italian_excel(self):
        rows = self.read_csv('ricerca.csv')
        self.assertEqual(len(rows), 5003 + 1)
        self.assertEqual(rows[0][:3], ['Carta', 'Nome alternativo', 'Espansione'])
        self.assertEqual(rows[0][8], 'Cash (\u20ac)')
        self.assertEqual(rows[1][0], 'Pok\u00e9mon & <Co>')
        self.assertEqual(rows[1][8], '1234,57')     # virgola decimale, due decimali
        self.assertEqual(rows[1][9], '0,00')
        self.assertEqual(rows[2][8], '')             # prezzo mancante resta vuoto
        self.assertTrue((self.out / 'ricerca.csv').read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_csv_blocks_spreadsheet_formulas_and_keeps_special_characters(self):
        rows = self.read_csv('ricerca.csv')
        self.assertEqual(rows[2][0], "'=SUM(1+1)")
        self.assertEqual(rows[2][1], "'+cmd")
        self.assertEqual(rows[2][2], "'-Esp")
        self.assertEqual(rows[2][3], "'@1")
        self.assertEqual(rows[3][0], 'Con;punto e virgola, e\nA capo')
        self.assertEqual(rows[3][11], 'i')           # carattere di controllo rimosso

    def test_csv_with_comma_separator_and_dot_decimal(self):
        rows = self.read_csv('ricerca_virgola.csv', delimiter=',')
        self.assertEqual(rows[1][8], '1234.57')

    def test_xlsx_is_a_valid_package(self):
        for name in ('ricerca.xlsx', 'vuoto.xlsx', 'senza_compressione.xlsx'):
            with zipfile.ZipFile(self.out / name) as package:
                self.assertIsNone(package.testzip(), name)
                for part in package.namelist():
                    minidom.parseString(package.read(part))   # XML ben formato

    def test_xlsx_content_types_and_layout(self):
        book = openpyxl.load_workbook(self.out / 'ricerca.xlsx')
        self.assertEqual(book.sheetnames, ['Buylist', 'Informazioni'])
        sheet = book['Buylist']
        self.assertEqual(sheet.max_row, 5004)
        self.assertEqual([c.value for c in sheet[1]][:3], ['Carta', 'Nome alternativo', 'Espansione'])
        self.assertEqual(sheet.freeze_panes, 'A2')
        self.assertEqual(sheet.auto_filter.ref, 'A1:L5004')
        first = [c.value for c in sheet[2]]
        self.assertEqual(first[0], 'Pok\u00e9mon & <Co>')
        self.assertIsNone(first[1])
        self.assertAlmostEqual(first[8], 1234.567)
        self.assertIsInstance(first[8], float)
        self.assertEqual(sheet['I2'].number_format, '#,##0.00')
        self.assertIsNone(sheet['I3'].value)
        self.assertEqual(sheet['J3'].value, 12.5)

    def test_xlsx_keeps_formula_like_text_as_text(self):
        sheet = openpyxl.load_workbook(self.out / 'ricerca.xlsx')['Buylist']
        self.assertEqual(sheet['A3'].value, '=SUM(1+1)')
        self.assertEqual(sheet['A3'].data_type, 's')
        self.assertEqual(sheet['A4'].value, 'Con;punto e virgola, e\nA capo')
        self.assertEqual(sheet['L4'].value, 'i')

    def test_xlsx_information_sheet_lists_filters(self):
        values = [[c.value for c in row] for row in openpyxl.load_workbook(self.out / 'ricerca.xlsx')['Informazioni'].iter_rows()]
        flat = [v for row in values for v in row if v]
        self.assertIn('Righe esportate', flat)
        self.assertIn('5003', flat)
        self.assertIn('Buono decrescente', flat)
        self.assertIn(['Lingua', 'Italiano, English'], [[v for v in row if v] for row in values])
        self.assertIn('25/11/2026 12:00', flat)

    def test_empty_export_still_has_header_only(self):
        sheet = openpyxl.load_workbook(self.out / 'vuoto.xlsx')['Buylist']
        self.assertEqual(sheet.max_row, 1)

    def test_too_many_rows_for_excel_is_refused_with_a_clear_message(self):
        message = (self.out / 'errore_limite.txt').read_text(encoding='utf-8')
        self.assertIn('usa il CSV', message)

    def test_file_name_has_date_and_time(self):
        self.assertEqual((self.out / 'nome.txt').read_text(), 'cgc-buylist_2026-11-25_0905.xlsx')


if __name__ == '__main__':
    unittest.main()