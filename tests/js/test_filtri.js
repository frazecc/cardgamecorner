/* Prova della logica dei filtri (docs/filtri-buylist.js) senza browser: node tests/js/test_filtri.js */
'use strict';
const assert = require('assert');
const F = require('../../docs/filtri-buylist.js');

const righe = F.prepara([
  { name: 'Pokémon Speciale', name_en: 'Special Pokemon', expansion: 'Base', serial_number: '1', language: 'Italiano', condition: 'NM/M', foil: '', first_edition: '', buylist: 1.5, credit: 3, status: 'PREZZO DISPONIBILE' },
  { name: 'Abra', name_en: null, expansion: 'Jungle', serial_number: '2', language: 'English', condition: 'SP', foil: 'Reverse Holo', first_edition: 'First Edition', buylist: 0.5, credit: 9, status: 'PREZZO DISPONIBILE' },
  { name: 'Zubat', name_en: null, expansion: 'Fossil', serial_number: '3', language: '', condition: 'HP', foil: '', first_edition: '', buylist: null, credit: null, status: 'VALUTAZIONE SU RICHIESTA' },
]);
const nomi = (l) => l.map((r) => r.name);
const nessuno = { name: '', expansion: '', serial: '', languages: [], conditions: [], foils: [], firstEditions: [], statuses: [], minCash: '', minBuono: '' };

// ricerca senza accenti, anche sul nome alternativo
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, name: 'pokemon' })), ['Pokémon Speciale']);
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, name: 'special pokemon' })), ['Pokémon Speciale']);
// una categoria senza caselle spuntate non filtra
assert.strictEqual(F.filtra(righe, nessuno).length, 3);
// lingue, compresa quella vuota
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, languages: ['English'] })), ['Abra']);
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, languages: [F.VUOTO] })), ['Zubat']);
// minimi: righe senza prezzo escluse
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, minCash: '0,01' })), ['Pokémon Speciale', 'Abra']);
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, minBuono: '5' })), ['Abra']);
assert.deepStrictEqual(nomi(F.filtra(righe, { ...nessuno, statuses: ['VALUTAZIONE SU RICHIESTA'] })), ['Zubat']);

// ordinamento
assert.deepStrictEqual(nomi(F.ordina(righe, 'buono_desc')), ['Abra', 'Pokémon Speciale', 'Zubat']);
assert.deepStrictEqual(nomi(F.ordina(righe, 'cash_desc')), ['Pokémon Speciale', 'Abra', 'Zubat']);
assert.deepStrictEqual(nomi(F.ordina(righe, 'name_asc')), ['Abra', 'Pokémon Speciale', 'Zubat']);
assert.strictEqual(F.ordina(righe, ''), righe);

// espansioni scelte, massimi, nuovi ordinamenti
const conEsp = F.prepara([
  { name: 'B', expansion: 'Base', language: 'Italiano', condition: 'SP', foil: '', first_edition: '', buylist: 2, credit: 4, status: 'PREZZO DISPONIBILE' },
  { name: 'A', expansion: 'Jungle', language: 'Italiano', condition: 'SP', foil: '', first_edition: '', buylist: 1, credit: 5, status: 'PREZZO DISPONIBILE' },
  { name: 'C', expansion: 'Fossil', language: 'Italiano', condition: 'SP', foil: '', first_edition: '', buylist: null, credit: null, status: 'PREZZO DISPONIBILE' },
]);
assert.strictEqual(F.filtra(conEsp, { ...nessuno, expansions: null }).length, 3);
assert.deepStrictEqual(nomi(F.filtra(conEsp, { ...nessuno, expansions: ['Jungle', 'Fossil'] })), ['A', 'C']);
assert.strictEqual(F.filtra(conEsp, { ...nessuno, expansions: [] }).length, 0);
assert.deepStrictEqual(nomi(F.filtra(conEsp, { ...nessuno, maxCash: '1,5' })), ['A']);
assert.deepStrictEqual(nomi(F.filtra(conEsp, { ...nessuno, maxBuono: '4' })), ['B']);
assert.deepStrictEqual(nomi(F.ordina(conEsp, 'name_desc')), ['C', 'B', 'A']);
assert.deepStrictEqual(nomi(F.ordina(conEsp, 'diff_desc')), ['A', 'B', 'C']);     // differenze 4 e 2, poi senza prezzi in fondo
assert.deepStrictEqual(nomi(F.ordina(conEsp, 'diff_asc')), ['B', 'A', 'C']);
assert.deepStrictEqual(nomi(F.ordina(conEsp, 'ratio_desc')), ['A', 'B', 'C']);    // 5 e 2
assert.deepStrictEqual(nomi(F.ordina(conEsp, 'exp_asc')), ['B', 'C', 'A']);
assert.deepStrictEqual(F.descrivi({ ...nessuno, expansions: ['Base'], maxCash: '2.5' }), [['Espansioni', 'Base'], ['Cash massimo (€)', '2,5']]);

// quali file dati servono
assert.deepStrictEqual(F.gruppiNecessari(['PREZZO DISPONIBILE']), ['prezzi']);
assert.deepStrictEqual(F.gruppiNecessari([]).sort(), ['escluse', 'prezzi', 'richiesta', 'senza-prezzo']);

// caselle dei filtri costruite dai dati
const lingue = F.opzioniLingua([['English', 5], ['Italiano', 9], ['Dutch', 1], ['', 2]]);
assert.deepStrictEqual(lingue.sinistra.map((o) => o.valore), ['Italiano', 'English']);
assert.deepStrictEqual(lingue.destra.map((o) => o.valore), ['Dutch', F.VUOTO]);
assert.deepStrictEqual(F.opzioniCondizione(['HP', 'NM/M', 'SP']).map((o) => o.valore), ['NM/M', 'SP', 'HP']);
assert.deepStrictEqual(F.opzioniFoil([['', 3], ['Holo', 1], ['Reverse Holo', 2]]).map((o) => o.valore), ['Reverse Holo', 'Holo']);

// testo del foglio "Informazioni"
assert.deepStrictEqual(F.descrivi({ ...nessuno, languages: ['Italiano'], minCash: '0.01' }), [['Lingua', 'Italiano'], ['Cash minimo (€)', '0,01']]);

console.log('test_filtri: tutto ok');
