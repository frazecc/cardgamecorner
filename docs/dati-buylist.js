/* Caricatore dei dati della buylist (file JSON generati ogni ora dal workflow "Pubblica il sito").
 *
 * Uso:
 *   const meta = await CGCDati.meta();                       // data di aggiornamento e conteggi
 *   const righe = await CGCDati.carica(['prezzi']);          // righe "piatte", una per variante
 *   const altre = await CGCDati.carica(['richiesta', 'escluse']);   // solo quando servono
 *
 * Ogni riga ha gli STESSI nomi di campo della vista cgc_buylist, quindi i filtri
 * esistenti del sito continuano a funzionare: name, name_en, expansion,
 * serial_number, image, language, condition, foil, first_edition, buylist, credit,
 * status, card_key. buylist e credit sono numeri (o null).
 * Gruppi disponibili: prezzi, richiesta, escluse, senza-prezzo.
 * meta.valori_filtri[gruppo].language / condition / foil / first_edition = [[valore, quante righe], ...]
 * per costruire le caselle dei filtri senza scaricare tutti i file ('' = campo vuoto).
 * Da una pagina in docs/nuovo/ chiama prima CGCDati.impostaBase('../data/').
 */
(function (root) {
  'use strict';
  var BASE = 'data/';
  var cache = {};

  /* Cartella dei dati rispetto alla pagina: 'data/' (default) oppure '../data/' per docs/nuovo/. */
  function impostaBase(percorso) {
    BASE = /\/$/.test(percorso) ? percorso : percorso + '/';
    cache = {};
  }

  function leggi(nome) {
    if (!cache[nome]) {
      cache[nome] = fetch(BASE + nome + '.json', { cache: 'no-cache' }).then(function (r) {
        if (!r.ok) throw new Error('Impossibile leggere ' + nome + ' (HTTP ' + r.status + ')');
        return r.json();
      });
      cache[nome].catch(function () { delete cache[nome]; });
    }
    return cache[nome];
  }

  /* Trasforma un file raggruppato per carta in righe piatte (una per variante). */
  function appiattisci(file) {
    var campi = file.campi_variante, righe = [];
    for (var i = 0; i < file.carte.length; i++) {
      var c = file.carte[i];
      for (var j = 0; j < c.v.length; j++) {
        var v = c.v[j], r = {
          card_key: c.k, name: c.n, name_en: c.en, expansion: c.e, serial_number: c.s, image: c.i
        };
        for (var k = 0; k < campi.length; k++) r[campi[k]] = v[k];
        righe.push(r);
      }
    }
    return righe;
  }

  function meta() { return leggi('meta'); }

  function carica(gruppi) {
    return Promise.all(gruppi.map(function (g) {
      return leggi(g).then(appiattisci);
    })).then(function (liste) { return [].concat.apply([], liste); });
  }

  root.CGCDati = { meta: meta, carica: carica, appiattisci: appiattisci, impostaBase: impostaBase };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.CGCDati;
})(typeof window !== 'undefined' ? window : globalThis);
