/* Filtri, ordinamento e testi per la tabella della buylist (tutto nel browser, senza database).
 *
 * Le righe sono quelle di CGCDati.carica(): campi name, name_en, expansion, serial_number, image,
 * language, condition, foil, first_edition, buylist, credit, status.
 *   CGCFiltri.prepara(righe)            aggiunge i testi di ricerca (una volta sola dopo il caricamento)
 *   CGCFiltri.filtra(righe, filtri)     -> righe che rispettano i filtri
 *   CGCFiltri.ordina(righe, ordine)     -> copia ordinata (buono_desc, buono_asc, cash_desc, cash_asc, name_asc, '')
 *   CGCFiltri.gruppiNecessari(stati)    -> quali file dati servono per gli stati scelti
 *   CGCFiltri.descrivi(filtri)          -> [['Lingua', 'Italiano, English'], ...] per il foglio "Informazioni"
 *   CGCFiltri.opzioniLingua / opzioniCondizione / opzioniFoil(valori) -> elenchi per le caselle dei filtri
 * filtri = { name, expansion, serial, languages[], conditions[], foils[], firstEditions[], statuses[], minCash, minBuono }
 * Come nel sito originale: una categoria senza caselle spuntate non filtra nulla.
 */
(function (root) {
  'use strict';
  var VUOTO = '__empty__';
  var GRUPPO_DI_STATO = {
    'PREZZO DISPONIBILE': 'prezzi', 'VALUTAZIONE SU RICHIESTA': 'richiesta',
    'SENZA PREZZO BUYLIST': 'senza-prezzo', 'ESCLUSA DALLA BUYLIST': 'escluse'
  };
  var LINGUE_IN_EVIDENZA = ['Italiano', 'English', 'Japanese', 'Chinese', 'Chinese Simplified', 'Chinese Traditional', 'Korean'];
  var CONDIZIONI_IN_ORDINE = ['NM/M', 'SP', 'HP', 'D'];
  var FOIL_IN_ORDINE = ['Reverse Holo', 'Holo'];
  var ETICHETTE_ORDINE = {
    buono_desc: 'Buono decrescente', buono_asc: 'Buono crescente', cash_desc: 'Cash decrescente',
    cash_asc: 'Cash crescente', name_asc: 'Nome A-Z', '': 'Nessun ordine'
  };

  function norm(s) {
    return String(s === null || s === undefined ? '' : s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }
  function insieme(lista) { var s = {}; (lista || []).forEach(function (v) { s[v] = true; }); return s; }
  function numero(v) {
    if (v === null || v === undefined || String(v).trim() === '') return null;
    var n = Number(String(v).replace(',', '.'));
    return isFinite(n) ? n : null;
  }

  function prepara(righe) {
    for (var i = 0; i < righe.length; i++) {
      var r = righe[i];
      r._n = norm(r.name) + '\u0001' + norm(r.name_en);
      r._e = norm(r.expansion);
      r._s = norm(r.serial_number);
    }
    return righe;
  }

  function filtra(righe, f) {
    var nome = norm(f.name).trim(), esp = norm(f.expansion).trim(), ser = norm(f.serial).trim();
    var lingue = insieme(f.languages), cond = insieme(f.conditions), foil = insieme(f.foils);
    var prime = insieme(f.firstEditions), stati = insieme(f.statuses);
    var haLingue = f.languages && f.languages.length, haCond = f.conditions && f.conditions.length;
    var haFoil = f.foils && f.foils.length, haPrime = f.firstEditions && f.firstEditions.length;
    var haStati = f.statuses && f.statuses.length;
    var minCash = numero(f.minCash), minBuono = numero(f.minBuono);
    return righe.filter(function (r) {
      if (nome && r._n.indexOf(nome) < 0) return false;
      if (esp && r._e.indexOf(esp) < 0) return false;
      if (ser && r._s.indexOf(ser) < 0) return false;
      if (haLingue && !lingue[r.language || VUOTO]) return false;
      if (haCond && !cond[r.condition || VUOTO]) return false;
      if (haFoil && !foil[r.foil || VUOTO]) return false;
      if (haPrime && !prime[r.first_edition || VUOTO]) return false;
      if (haStati && !stati[r.status]) return false;
      if (minCash !== null && !(r.buylist !== null && r.buylist >= minCash)) return false;
      if (minBuono !== null && !(r.credit !== null && r.credit >= minBuono)) return false;
      return true;
    });
  }

  function valore(r, campo) { return r[campo] === null || r[campo] === undefined ? 0 : Number(r[campo]) || 0; }
  function perNome(a, b) { return String(a.name || '').localeCompare(String(b.name || ''), 'it', { sensitivity: 'base' }); }

  function ordina(righe, ordine) {
    if (!ordine || !righe.length) return righe;
    var copia = righe.slice(), cmp;
    if (ordine === 'buono_desc') cmp = function (a, b) { return valore(b, 'credit') - valore(a, 'credit') || valore(b, 'buylist') - valore(a, 'buylist') || perNome(a, b); };
    else if (ordine === 'buono_asc') cmp = function (a, b) { return valore(a, 'credit') - valore(b, 'credit') || valore(a, 'buylist') - valore(b, 'buylist') || perNome(a, b); };
    else if (ordine === 'cash_desc') cmp = function (a, b) { return valore(b, 'buylist') - valore(a, 'buylist') || valore(b, 'credit') - valore(a, 'credit') || perNome(a, b); };
    else if (ordine === 'cash_asc') cmp = function (a, b) { return valore(a, 'buylist') - valore(b, 'buylist') || valore(a, 'credit') - valore(b, 'credit') || perNome(a, b); };
    else if (ordine === 'name_asc') cmp = perNome;
    else return righe;
    return copia.sort(cmp);   // sort e stabile: a parita resta l'ordine originale
  }

  function gruppiNecessari(stati) {
    var scelti = (stati && stati.length) ? stati : Object.keys(GRUPPO_DI_STATO);
    var gruppi = [];
    scelti.forEach(function (s) { var g = GRUPPO_DI_STATO[s]; if (g && gruppi.indexOf(g) < 0) gruppi.push(g); });
    return gruppi;
  }

  /* ---- elenchi per le caselle (valori = [[valore, conteggio], ...] oppure semplici testi) ---- */
  function soloValori(valori) { return (valori || []).map(function (v) { return Array.isArray(v) ? v[0] : v; }); }
  function unici(valori) { var visti = {}, out = []; soloValori(valori).forEach(function (v) { if (!visti[v]) { visti[v] = true; out.push(v); } }); return out; }
  function alfabetico(a, b) { return a.localeCompare(b, 'en', { sensitivity: 'base' }); }
  function conPriorita(valori, priorita) {
    var tutti = unici(valori).filter(function (v) { return v !== ''; });
    var testa = priorita.filter(function (v) { return tutti.indexOf(v) >= 0; });
    var coda = tutti.filter(function (v) { return priorita.indexOf(v) < 0; }).sort(alfabetico);
    return { testa: testa, coda: coda, haVuoto: unici(valori).indexOf('') >= 0 };
  }

  /* Lingua: colonna sinistra le piu usate, destra tutte le altre in ordine alfabetico. */
  function opzioniLingua(valori) {
    var o = conPriorita(valori, LINGUE_IN_EVIDENZA);
    var destra = o.coda.map(function (v) { return { valore: v, etichetta: v }; });
    if (o.haVuoto) destra.push({ valore: VUOTO, etichetta: 'Non specificata' });
    return { sinistra: o.testa.map(function (v) { return { valore: v, etichetta: v }; }), destra: destra };
  }
  function opzioniCondizione(valori) {
    var o = conPriorita(valori, CONDIZIONI_IN_ORDINE), out = o.testa.concat(o.coda).map(function (v) { return { valore: v, etichetta: v }; });
    if (o.haVuoto) out.push({ valore: VUOTO, etichetta: 'Non specificata' });
    return out;
  }
  function opzioniFoil(valori) {
    var o = conPriorita(valori, FOIL_IN_ORDINE);
    return o.testa.concat(o.coda).map(function (v) { return { valore: v, etichetta: v }; });
  }

  /* ---- testo dei filtri attivi, per il foglio "Informazioni" dell'esportazione ---- */
  function descrivi(f, ordine) {
    var out = [];
    function testo(nome, v) { if (v && String(v).trim()) out.push([nome, String(v).trim()]); }
    function elenco(nome, v) { if (v && v.length) out.push([nome, v.map(function (x) { return x === VUOTO ? '(vuoto)' : x; }).join(', ')]); }
    testo('Nome carta', f.name); testo('Espansione', f.expansion); testo('Numero / Serial', f.serial);
    elenco('Lingua', f.languages); elenco('Condizione', f.conditions); elenco('Foil', f.foils);
    elenco('1\u00aa edizione', (f.firstEditions || []).map(function (x) { return x === VUOTO ? 'No' : 'S\u00ec'; }));
    elenco('Stato offerta', f.statuses);
    if (numero(f.minCash) !== null) out.push(['Cash minimo (\u20ac)', String(numero(f.minCash)).replace('.', ',')]);
    if (numero(f.minBuono) !== null) out.push(['Buono minimo (\u20ac)', String(numero(f.minBuono)).replace('.', ',')]);
    return out;
  }

  root.CGCFiltri = {
    VUOTO: VUOTO, GRUPPO_DI_STATO: GRUPPO_DI_STATO, ETICHETTE_ORDINE: ETICHETTE_ORDINE,
    norm: norm, prepara: prepara, filtra: filtra, ordina: ordina, gruppiNecessari: gruppiNecessari,
    opzioniLingua: opzioniLingua, opzioniCondizione: opzioniCondizione, opzioniFoil: opzioniFoil, descrivi: descrivi
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.CGCFiltri;
})(typeof window !== 'undefined' ? window : globalThis);
