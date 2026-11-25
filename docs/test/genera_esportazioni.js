// Genera file di prova con il modulo docs/esporta-ricerca.js (usato da test_esporta_ricerca.py).
const fs = require('fs');
const path = require('path');
const E = require(path.join(__dirname, '../../docs/esporta-ricerca.js'));
const out = process.argv[2];

const righe = [
  { name: 'Pok\u00e9mon & <Co>', name_en: null, expansion: 'Base "Set"', serial_number: '004/102', language: 'Italiano',
    condition: 'NM/M', foil: '', first_edition: 'First Edition', buylist: 1234.567, credit: 0, status: 'PREZZO DISPONIBILE', image: 'https://x.invalid/a.jpg' },
  { name: '=SUM(1+1)', name_en: '+cmd', expansion: '-Esp', serial_number: '@1', language: 'Giapponese \u30d4\u30ab\u30c1\u30e5\u30a6',
    condition: 'SP', foil: 'Reverse Holo', first_edition: '', buylist: null, credit: 12.5, status: 'VALUTAZIONE SU RICHIESTA', image: '' },
  { name: 'Con;punto e virgola, e\nA capo', name_en: 'x', expansion: 'Y', serial_number: '1', language: 'English',
    condition: 'HP', foil: '', first_edition: '', buylist: '3.1', credit: '', status: 'SENZA PREZZO BUYLIST', image: 'i\u0001' }
];
for (let i = 0; i < 5000; i++) {
  righe.push({ name: 'Carta ' + (i % 700), name_en: null, expansion: 'Esp ' + (i % 30), serial_number: String(i), language: i % 2 ? 'Italiano' : 'English',
    condition: ['NM/M', 'SP', 'HP', 'D'][i % 4], foil: '', first_edition: '', buylist: i / 10, credit: i / 20, status: 'PREZZO DISPONIBILE', image: '' });
}
const opzioni = { aggiornatoAl: '25/11/2026 12:00', ordinamento: 'Buono decrescente',
  filtri: [['Lingua', 'Italiano, English'], ['Cash minimo', '0,01']] };

(async () => {
  fs.writeFileSync(path.join(out, 'ricerca.csv'), Buffer.from(await E.csv(righe, opzioni).arrayBuffer()));
  fs.writeFileSync(path.join(out, 'ricerca_virgola.csv'), Buffer.from(await E.csv(righe.slice(0, 3), { separatore: ',', decimale: '.' }).arrayBuffer()));
  fs.writeFileSync(path.join(out, 'ricerca.xlsx'), Buffer.from(await (await E.xlsx(righe, opzioni)).arrayBuffer()));
  fs.writeFileSync(path.join(out, 'vuoto.xlsx'), Buffer.from(await (await E.xlsx([], {})).arrayBuffer()));
  const comp = globalThis.CompressionStream;
  delete globalThis.CompressionStream;       // browser senza compressione: il file deve restare valido
  fs.writeFileSync(path.join(out, 'senza_compressione.xlsx'), Buffer.from(await (await E.xlsx(righe.slice(0, 10), opzioni)).arrayBuffer()));
  globalThis.CompressionStream = comp;
  let errore = null;
  try { await E.xlsx(new Array(E.maxRigheXlsx + 1).fill(righe[0])); } catch (e) { errore = e.message; }
  fs.writeFileSync(path.join(out, 'errore_limite.txt'), errore || '');
  fs.writeFileSync(path.join(out, 'nome.txt'), E.nomeFile('xlsx', new Date(2026, 10, 25, 9, 5)));
})().catch(e => { console.error(e); process.exit(1); });
