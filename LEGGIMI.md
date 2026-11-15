# CardGameCorner Buylist Pokémon — versione 3.0.0

Sostituisci nel repository i due file contenuti nello ZIP, mantenendo i percorsi:
- scraper.py
- .github/workflows/scrape.yml

Apri Actions > Estrai Buylist CardGameCorner > Run workflow sul branch main.
Alla fine scarica l'artifact Buylist-Pokemon-Excel. Il workflow aggiornato conserva
anche log, checkpoint e file parziali nell'artifact Buylist-Pokemon-Diagnostica.

## Copertura

Una ricerca vuota sul sito restituisce una selezione: nell'esecuzione controllata
ha prodotto 226 carte in 12 pagine. La ricerca dell'espansione Base ha mostrato
altre carte con prezzi buylist. Perciò la versione 3 percorre prima la ricerca
generale, poi OGNI espansione presente nel filtro Edizione, leggendo TUTTE le
pagine di ogni ricerca. Include espansioni giapponesi e promozionali quando sono
presenti nel filtro. Non impone limiti al numero di carte o di pagine.

Nel foglio Espansioni trovi le ricerche completate e i conteggi. Il file viene
chiamato Completa solo dopo aver terminato tutte le ricerche previste. Questo
certifica la copertura dei risultati pubblicati attraverso quei filtri, non
l'esistenza di carte che il sito non restituisce o non pubblica.

## Prezzi e varianti

Il foglio Carte contiene la lingua e la condizione selezionate dal sito per
ciascun prodotto, con il prezzo visualizzato e i campi MinAcquisto/MaxAcquisto
originali. Non bisogna interpretarli come prezzi validi per tutte le condizioni,
lingue o varianti. Le lingue e condizioni disponibili sono elencate. Il foglio
Dati_originali conserva tutti i campi dei prodotti caricati; le lunghe stringhe
JSON sono suddivise in parti per evitare il limite delle celle Excel.
Questa versione non enumera e non ricalcola ogni combinazione dei selettori.

## Affidabilità

Controlla filtri, paginazione consecutiva e stabilità del numero di pagine per
ciascuna ricerca. Deduplica con gli identificatori dei prodotti, senza fondere
carte diverse solo perché hanno lo stesso nome. In caso di timeout attende ancora
senza cliccare due volte e rischiare di saltare una pagina. Su errore termina con
codice non zero e conserva una copia PARZIALE, senza presentarla come completa.
Il giornale JSONL viene aggiornato dopo ogni pagina; la copia Excel parziale
viene aggiornata ogni dieci ricerche e sugli errori intercettati. Il giornale è
un checkpoint diagnostico: la ripresa automatica non è implementata.

## Verifiche svolte

- Sintassi Python e controlli sui filtri verificati.
- Generazione e riapertura Excel con rapporto di copertura verificate.
- Sul sito è stata verificata la paginazione e l'insufficienza della ricerca
  generale confrontandola con l'espansione Base.
- La scansione completa di tutte le espansioni della versione 3 deve ancora
  essere eseguita. Non è stato verificato un conteggio finale completo.

## Uso locale

    python -m pip install playwright pandas openpyxl
    python -m playwright install --with-deps chromium
    python scraper.py

Per vedere il browser: python scraper.py --headed
Per aumentare il timeout per pagina: python scraper.py --timeout 180

Non viene effettuato login e non vengono aggiunti prodotti al carrello.
