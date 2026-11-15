# CardGameCorner Buylist Pokémon — versione 5.0.1

Aggiorna scraper.py, sheets_sync.py, requirements.txt, tests/ e
.github/workflows/scrape.yml mantenendo i percorsi e avvia un NUOVO Run workflow.
Non usare Re-run jobs di un vecchio commit.

## Google Sheets e prestazioni

IMPORT CGC è già selezionato con l'ID fornito. Leggi GOOGLE_SHEETS.md per
aggiungere il solo Secret GOOGLE_SERVICE_ACCOUNT_JSON e condividere il documento
con il relativo account di servizio: l'importazione sarà automatica al termine della scansione. Quattro schede
CGC_ ricevono dati verificati in blocchi, senza modificare le altre schede.
Un Excel parziale non sostituisce il risultato completo nel documento.

La versione 5 controlla il caricamento con dati leggeri, trasferisce il JSON solo
per le nuove carte, evita immagini/font/video e riscrive meno spesso il parziale.
I link delle immagini restano nel risultato. Il workflow consente anche di
importare un Excel completo precedente senza rifare lo scraping (publish_only).

## Tutti i prezzi pubblicati

Il foglio principale Prezzi contiene ogni voce dell'elenco Variants restituito
per ciascuna carta, con:

- ID carta e ID variante, nome, espansione, numero e immagine;
- lingua e condizione esatte del sito;
- foil/Reverse Holo e prima edizione esattamente come pubblicati;
- BuyList e Credit in due colonne numeriche separate;
- valutazione su richiesta, esclusione dalla buylist, prezzi zero e dati grezzi
  BuyListLock/Quantity, senza scartare varianti.

Non calcola i prezzi moltiplicando il valore Near Mint e non genera combinazioni
inesistenti. Se il sito pubblica solo alcune condizioni per una lingua, conserva
quelle: le altre non hanno una quotazione pubblicata. ID variante diversi restano
righe distinte anche quando lingua, condizione ed edizione coincidono.

La stessa carta può comparire in più ricerche: le varianti si uniscono per ID,
senza perdere quelle viste in precedenza. Per lo stesso ID e gli stessi attributi
si conserva l'ultimo prezzo osservato. Cambiamenti di lingua/condizione/foil/prima
edizione sotto lo stesso ID fanno interrompere l'esportazione. Dati Variants
mancanti, JSON illeggibile o prezzi non validi non vengono ignorati.

Il foglio Carte contiene l'anagrafica e i conteggi delle varianti. Pagine ed
Espansioni documentano la scansione, Info spiega campi e copertura, Dati_originali
conserva i prodotti e le varianti raccolte in JSON, suddiviso in parti se necessario.

## Copertura delle carte

Percorre la ricerca generale e OGNI espansione disponibile nel filtro Edizione,
seguendo TUTTE le pagine. La ricerca generale da sola aveva restituito solo 226
carte, mentre le ricerche per espansione ne hanno trovate altre. Non impone un
limite al numero di carte, lingue, condizioni, varianti o pagine.

Il file Completa viene creato solo se tutte le ricerche sono terminate e l'elenco
delle espansioni non è cambiato. La copertura riguarda i dati pubblicati dal sito
attraverso questi risultati; non include prezzi che il sito non fornisce.

## Ricerche vuote ed errori

Per il caso Awakened Heroes usa TotalRecords=0, zero pagine, nessuna riga,
nessuna richiesta pendente, risposta HTTP riuscita, filtro corretto e stabilità.
Attende una nuova risposta anche con risultati non vuoti e azzera l'attesa di
stabilità quando lo stato cambia. Errori HTTP/rete non sono risultati vuoti.

Su errore conserva il file PARZIALE e la diagnostica e termina con codice diverso
da zero. Il JSONL viene aggiornato dopo ogni pagina, l'Excel parziale ogni quindici minuti e sugli errori intercettati. La ripresa automatica non è implementata:
una nuova esecuzione riparte dall'inizio.

## Verifiche

- 42 test automatici, compresi il timeout reale e la gestione delle varianti.
- Estrazione verificata sul checkpoint reale: 2.165 carte, 11.519 varianti.
- Esportazione e rilettura Excel: verificati tutti gli ID, le lingue, le condizioni,
  i prezzi buylist e i crediti delle 11.519 varianti, senza differenze.
- Confronto sul sito: Charizard, Italiano, Excellent, First Edition mostra
  BuyList 1879.95 e Credit 2519.95, come nei dati Variants.
- La scansione completa di tutte le espansioni con la versione 5.0.1 deve ancora
  terminare su GitHub Actions. I numeri sopra si riferiscono al parziale reale.

Per ripetere i test dalla radice del pacchetto:

    python -m unittest discover -s tests -v

## Uso locale

    python -m pip install -r requirements.txt
    python -m playwright install --with-deps chromium
    python scraper.py

Browser visibile: python scraper.py --headed
Timeout per pagina: python scraper.py --timeout 180

Non serve un account. Non vengono aggiunte carte al carrello.

I test Google Sheets usano un modello API per verificare blocchi, quote, risposta persa, rifiuto dei parziali, pubblicazioni ripetute e conservazione delle schede precedenti. Non attestano una pubblicazione nel tuo account senza configurazione.
