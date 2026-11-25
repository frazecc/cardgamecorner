/* Esporta in CSV o Excel (.xlsx) TUTTE le righe della ricerca attuale (non solo la pagina visibile).
 * Nessuna libreria esterna: funziona su GitHub Pages cosi com'e.
 *
 * Uso (righe = l'elenco gia filtrato e ordinato, con i campi di cgc_buylist):
 *   CGCEsporta.csv(righe, opzioni)     -> Blob  (CSV per Excel italiano: separatore ; e virgola decimale)
 *   CGCEsporta.xlsx(righe, opzioni)    -> Promise<Blob>
 *   CGCEsporta.scarica(blob, nomeFile) -> avvia il download
 *   CGCEsporta.nomeFile('xlsx')        -> es. cgc-buylist_2026-11-25_1230.xlsx
 *
 * opzioni (tutte facoltative):
 *   aggiornatoAl : testo con la data dei dati (da meta.json)
 *   filtri       : [['Lingua', 'Italiano, English'], ['Cash minimo', '0,01']]  (foglio "Informazioni")
 *   ordinamento  : testo, es. 'Buono decrescente'
 *   separatore   : solo CSV, default ';'  (usa ',' per Excel in inglese)
 *   decimale     : solo CSV, default ','
 * Il CSV protegge dalle "formule" nei testi (celle che iniziano con = + - @).
 */
(function (root) {
  'use strict';

  var COLONNE = [
    { t: 'Carta', k: 'name', w: 34 }, { t: 'Nome alternativo', k: 'name_en', w: 30 },
    { t: 'Espansione', k: 'expansion', w: 30 }, { t: 'Numero', k: 'serial_number', w: 10 },
    { t: 'Lingua', k: 'language', w: 18 }, { t: 'Condizione', k: 'condition', w: 12 },
    { t: 'Foil', k: 'foil', w: 14 }, { t: 'Prima edizione', k: 'first_edition', w: 16 },
    { t: 'Cash (\u20ac)', k: 'buylist', n: true, w: 12 }, { t: 'Buono (\u20ac)', k: 'credit', n: true, w: 12 },
    { t: 'Stato', k: 'status', w: 26 }, { t: 'Immagine', k: 'image', w: 40 }
  ];
  var MAX_RIGHE_XLSX = 100000;   // oltre, il browser fa fatica: meglio il CSV
  var CONTROLLO = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g;

  function testo(v) { return v === null || v === undefined ? '' : String(v); }
  function numero(v) { var n = (v === null || v === undefined || v === '') ? NaN : Number(v); return isFinite(n) ? n : null; }
  function due(n) { return (n < 10 ? '0' : '') + n; }

  function nomeFile(estensione, quando) {
    var d = quando || new Date();
    return 'cgc-buylist_' + d.getFullYear() + '-' + due(d.getMonth() + 1) + '-' + due(d.getDate()) +
      '_' + due(d.getHours()) + due(d.getMinutes()) + '.' + estensione;
  }

  /* ---------------- CSV ---------------- */
  function cellaCsv(valore, sep) {
    var s = valore;
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;          // niente formule dentro Excel
    if (s.indexOf('"') >= 0 || s.indexOf(sep) >= 0 || /[\r\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function csv(righe, opzioni) {
    var o = opzioni || {}, sep = o.separatore || ';', dec = o.decimale || ',';
    var righeTesto = [COLONNE.map(function (c) { return cellaCsv(c.t, sep); }).join(sep)];
    for (var i = 0; i < righe.length; i++) {
      var r = righe[i], celle = [];
      for (var j = 0; j < COLONNE.length; j++) {
        var c = COLONNE[j];
        if (c.n) { var n = numero(r[c.k]); celle.push(n === null ? '' : n.toFixed(2).replace('.', dec)); }
        else celle.push(cellaCsv(testo(r[c.k]).replace(CONTROLLO, ''), sep));
      }
      righeTesto.push(celle.join(sep));
    }
    return new Blob(['\uFEFF' + righeTesto.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
  }

  /* ---------------- XLSX (zip scritto a mano) ---------------- */
  var TAVOLA = (function () {
    var t = new Uint32Array(256);
    for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
  })();
  function crc32(byte) { var c = 0xFFFFFFFF; for (var i = 0; i < byte.length; i++) c = TAVOLA[(c ^ byte[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }

  function comprimi(byte) {
    if (typeof CompressionStream === 'undefined') return Promise.resolve(null);
    var flusso = new Blob([byte]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Response(flusso).arrayBuffer().then(function (b) { return new Uint8Array(b); }, function () { return null; });
  }

  function zip(file) {   // file: [{nome, dati: Uint8Array}]
    var enc = new TextEncoder(), ora = new Date();
    var dosOra = (ora.getHours() << 11) | (ora.getMinutes() << 5) | (ora.getSeconds() >> 1);
    var dosData = ((ora.getFullYear() - 1980) << 9) | ((ora.getMonth() + 1) << 5) | ora.getDate();
    return Promise.all(file.map(function (f) {
      return comprimi(f.dati).then(function (c) {
        return { nome: enc.encode(f.nome), crc: crc32(f.dati), usize: f.dati.length,
                 metodo: c && c.length < f.dati.length ? 8 : 0, dati: c && c.length < f.dati.length ? c : f.dati };
      });
    })).then(function (voci) {
      var pezzi = [], centrale = [], offset = 0;
      voci.forEach(function (v) {
        var h = new DataView(new ArrayBuffer(30));
        h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, v.metodo, true);
        h.setUint16(10, dosOra, true); h.setUint16(12, dosData, true); h.setUint32(14, v.crc, true);
        h.setUint32(18, v.dati.length, true); h.setUint32(22, v.usize, true); h.setUint16(26, v.nome.length, true); h.setUint16(28, 0, true);
        pezzi.push(new Uint8Array(h.buffer), v.nome, v.dati);
        var d = new DataView(new ArrayBuffer(46));
        d.setUint32(0, 0x02014b50, true); d.setUint16(4, 20, true); d.setUint16(6, 20, true); d.setUint16(8, 0x0800, true);
        d.setUint16(10, v.metodo, true); d.setUint16(12, dosOra, true); d.setUint16(14, dosData, true); d.setUint32(16, v.crc, true);
        d.setUint32(20, v.dati.length, true); d.setUint32(24, v.usize, true); d.setUint16(28, v.nome.length, true);
        d.setUint32(42, offset, true);
        centrale.push(new Uint8Array(d.buffer), v.nome);
        offset += 30 + v.nome.length + v.dati.length;
      });
      var dimCentrale = centrale.reduce(function (s, p) { return s + p.length; }, 0);
      var fine = new DataView(new ArrayBuffer(22));
      fine.setUint32(0, 0x06054b50, true); fine.setUint16(8, voci.length, true); fine.setUint16(10, voci.length, true);
      fine.setUint32(12, dimCentrale, true); fine.setUint32(16, offset, true);
      return new Blob(pezzi.concat(centrale, [new Uint8Array(fine.buffer)]),
        { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    });
  }

  function esc(s) {
    return s.replace(CONTROLLO, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function colonna(i) { var s = ''; for (i++; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + (i - 1) % 26) + s; return s; }

  function Stringhe() {
    var mappa = new Map(), lista = [], usi = 0;
    this.id = function (s) { usi++; var i = mappa.get(s); if (i === undefined) { i = lista.length; mappa.set(s, i); lista.push(s); } return i; };
    this.xml = function () {
      return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="' +
        usi + '" uniqueCount="' + lista.length + '">' + lista.map(function (s) {
          return '<si><t xml:space="preserve">' + esc(s) + '</t></si>';
        }).join('') + '</sst>';
    };
  }

  var INTESTAZIONE = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
  var NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"';

  function fogliDati(righe, sst) {
    var ultima = colonna(COLONNE.length - 1), n = righe.length + 1, x = [];
    x.push(INTESTAZIONE + '<worksheet ' + NS + '><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>');
    x.push('<cols>' + COLONNE.map(function (c, i) { return '<col min="' + (i + 1) + '" max="' + (i + 1) + '" width="' + c.w + '" customWidth="1"/>'; }).join('') + '</cols><sheetData>');
    x.push('<row r="1">' + COLONNE.map(function (c, i) { return '<c r="' + colonna(i) + '1" t="s" s="1"><v>' + sst.id(c.t) + '</v></c>'; }).join('') + '</row>');
    for (var i = 0; i < righe.length; i++) {
      var r = righe[i], rr = i + 2, celle = '';
      for (var j = 0; j < COLONNE.length; j++) {
        var c = COLONNE[j], ref = colonna(j) + rr;
        if (c.n) { var v = numero(r[c.k]); if (v !== null) celle += '<c r="' + ref + '" s="2"><v>' + v + '</v></c>'; }
        else { var t = testo(r[c.k]); if (t !== '') celle += '<c r="' + ref + '" t="s"><v>' + sst.id(t) + '</v></c>'; }
      }
      x.push('<row r="' + rr + '">' + celle + '</row>');
    }
    x.push('</sheetData><autoFilter ref="A1:' + ultima + n + '"/></worksheet>');
    return x.join('');
  }

  function foglioInfo(righe, o, sst) {
    var info = [['Buylist CardGameCorner \u2013 esportazione'], ['Esportato il', new Date().toLocaleString('it-IT')]];
    if (o.aggiornatoAl) info.push(['Dati aggiornati al', o.aggiornatoAl]);
    info.push(['Righe esportate', String(righe.length)]);
    if (o.ordinamento) info.push(['Ordinamento', o.ordinamento]);
    info.push([''], ['Filtri applicati']);
    var filtri = o.filtri && o.filtri.length ? o.filtri : [['Nessun filtro']];
    filtri.forEach(function (f) { info.push(f.map(testo)); });
    var x = [INTESTAZIONE + '<worksheet ' + NS + '><cols><col min="1" max="1" width="28" customWidth="1"/><col min="2" max="2" width="60" customWidth="1"/></cols><sheetData>'];
    info.forEach(function (riga, i) {
      x.push('<row r="' + (i + 1) + '">' + riga.map(function (t, j) {
        return t === '' ? '' : '<c r="' + colonna(j) + (i + 1) + '" t="s"' + (j === 0 && (i === 0 || riga.length === 1) ? ' s="3"' : '') + '><v>' + sst.id(t) + '</v></c>';
      }).join('') + '</row>');
    });
    x.push('</sheetData></worksheet>');
    return x.join('');
  }

  var STILI = INTESTAZIONE + '<styleSheet ' + NS + '><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>' +
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>' +
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>' +
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE8E8E8"/><bgColor indexed="64"/></patternFill></fill></fills>' +
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>' +
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
    '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' +
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>' +
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' +
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>' +
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>';

  function xlsx(righe, opzioni) {
    var o = opzioni || {};
    if (righe.length > MAX_RIGHE_XLSX) {
      return Promise.reject(new Error('Troppe righe per Excel (' + righe.length + ', massimo ' + MAX_RIGHE_XLSX + '): usa il CSV oppure restringi i filtri.'));
    }
    var enc = new TextEncoder(), sst = new Stringhe();
    var dati = fogliDati(righe, sst), info = foglioInfo(righe, o, sst);
    var R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    var parti = [
      ['[Content_Types].xml', INTESTAZIONE + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>'],
      ['_rels/.rels', INTESTAZIONE + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="' + R + '/officeDocument" Target="xl/workbook.xml"/></Relationships>'],
      ['xl/workbook.xml', INTESTAZIONE + '<workbook ' + NS + ' xmlns:r="' + R + '"><sheets><sheet name="Buylist" sheetId="1" r:id="rId1"/>' +
        '<sheet name="Informazioni" sheetId="2" r:id="rId2"/></sheets></workbook>'],
      ['xl/_rels/workbook.xml.rels', INTESTAZIONE + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="' + R + '/worksheet" Target="worksheets/sheet1.xml"/>' +
        '<Relationship Id="rId2" Type="' + R + '/worksheet" Target="worksheets/sheet2.xml"/>' +
        '<Relationship Id="rId3" Type="' + R + '/styles" Target="styles.xml"/>' +
        '<Relationship Id="rId4" Type="' + R + '/sharedStrings" Target="sharedStrings.xml"/></Relationships>'],
      ['xl/styles.xml', STILI], ['xl/worksheets/sheet1.xml', dati], ['xl/worksheets/sheet2.xml', info],
      ['xl/sharedStrings.xml', sst.xml()]
    ];
    return zip(parti.map(function (p) { return { nome: p[0], dati: enc.encode(p[1]) }; }));
  }

  function scarica(blob, nome) {
    var url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = nome; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }

  root.CGCEsporta = { csv: csv, xlsx: xlsx, scarica: scarica, nomeFile: nomeFile, colonne: COLONNE, maxRigheXlsx: MAX_RIGHE_XLSX };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.CGCEsporta;
})(typeof window !== 'undefined' ? window : globalThis);
