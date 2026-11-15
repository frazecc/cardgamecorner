# CardGameCorner Buylist Pokémon — versione 6.0.0

La versione 6 aggiunge il backup Google DURANTE la raccolta: normalmente 50 carte
per blocco e invio anche dopo 30 secondi con un blocco incompleto. La seconda
copia è in CGC_LIVE_Prezzi; lo stato è in CGC_LIVE_Info. Il checkpoint locale
continua a essere aggiornato dopo ogni pagina e sincronizzato su disco.

Quando il backup live termina e i conteggi coincidono, CGC_LIVE_Info diventa
COMPLETO. Il workflow evita una nuova copia finale di milioni di celle. I fogli
CGC_ e CGC_PARZIALE_ delle versioni precedenti rimangono disponibili ma possono
contenere dati di esecuzioni precedenti: per il nuovo run guarda CGC_LIVE_.


Per riprendere il run interrotto: dopo aver aggiornato tutti i file, apri Run
workflow e imposta resume_scrape_run_id=43737659445. Lascia publish_only e
partial_data disattivati e source_run_id vuoto. Riparte dalla ricerca 316/334.


Aggiorna scraper.py, sheets_sync.py, live_sheets.py, requirements.txt, tests/ e
.github/workflows/scrape.yml mantenendo i percorsi e avvia un NUOVO Run workflow.
Non usare Re-run jobs di un vecchio commit.

## Google Sheets e prestazioni

IMPORT CGC è già selezionato con l'ID fornito. Leggi GOOGLE_SHEETS.md per
aggiungere il solo Secret GOOGLE_SERVICE_ACCOUNT_JSON e condividere il documento
con il relativo account di servizio: l'importazione sarà automatica al termine della scansione. Quattro schede
CGC_ ricevono dati verificati in blocchi, senza modificare le altre schede.
In caso di errore, l'Excel parziale viene importato nelle schede CGC_PARZIALE_,
che restano separate dalle schede CGC_ contenenti il risultato completo.

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
si conserva l'ultima osservazione, mantenendo anche più voci diverse nella stessa
risposta. ID ripetuti con dati diversi restano separati: la colonna Chiave voce
li distingue e ID ripetuto nella carta li segnala. Non significa necessariamente
che siano varianti fisiche diverse. Dati Variants
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
da zero. Il JSONL viene aggiornato dopo ogni pagina, l'Excel parziale ogni quindici minuti e sugli errori intercettati. Una nuova esecuzione riparte dall'inizio salvo richiesta esplicita di ripresa:
usa resume_scrape_run_id nel workflow oppure --resume-checkpoint in locale.

## Verifiche

- 74 test automatici, compresi il timeout reale e la gestione delle varianti.
- Estrazione verificata sul checkpoint reale: 2.165 carte, 11.519 varianti.
- Esportazione e rilettura Excel: verificati tutti gli ID, le lingue, le condizioni,
  i prezzi buylist e i crediti delle 11.519 varianti, senza differenze.
- Confronto sul sito: Charizard, Italiano, Excellent, First Edition mostra
  BuyList 1879.95 e Credit 2519.95, come nei dati Variants.
- La scansione completa di tutte le espansioni con la versione 6.0.0 deve ancora
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


## Correzione 5.1.0 e recupero dei dati già raccolti

Il run GitHub 43728814267, versione 5.0.1, si è interrotto in Base con:
ID variante duplicato con dati diversi: 1032429. Il controllo aggiunto era troppo
restrittivo: ora conserva le voci diverse, senza scegliere o scartare un prezzo.
Solo le ripetizioni identiche dell'intera voce vengono collassate.

Corretta anche la diagnostica READ_STATE: Playwright passa null quando manca
l'argomento, perciò il default JavaScript da solo non bastava. Ora accetta null
e salva anche l'ultima riga elaborata. Il run originale non ha salvato il payload
della riga duplicata: i test usano coppie sintetiche con lo stesso ID per coprire
prezzi, condizioni, lingue e altri campi diversi, senza attribuire al caso reale
una differenza che non è stata osservata.

50 test superati. Verificata la lettura del parziale reale: 2.426 carte e 13.576
voci prezzo. Il controllo Google di quel run ha verificato autenticazione e
lettura; la scrittura effettiva rimane da verificare sul tuo account.

Per importare subito quel parziale, dopo avere caricato TUTTO questo pacchetto,
apri Actions > Estrai Buylist CardGameCorner > Run workflow e imposta:

- publish_only: attivo
- partial_data: attivo
- source_run_id: 43728814267

Il risultato comparirà in CGC_PARZIALE_Prezzi e nelle altre schede CGC_PARZIALE_.
Per una nuova scansione completa lascia publish_only e partial_data disattivati.


## Correzione 5.2.0 — metadati vuoti, errore Google 500 e ripresa

Il run 43737659445 ha completato 315 ricerche su 334 e si è interrotto su
Trade Please! (UNP0170), variante 1971737: Language=Japanese, Condition="",
BuyList=0, Credit=0, Evaluation=true. La condizione è assente nei dati pubblicati
per una valutazione su richiesta: non è motivo per scartare la voce o fermarsi.

Ora le dimensioni vuote/null/mancanti si conservano senza assegnare valori
inventati. Le colonne Lingua specificata dal sito e Condizione specificata dal
sito le segnalano. Prezzi non validi o JSON illeggibile continuano a fermare la
scansione: non si trasformano dati invalidi in prezzi zero.

Google aveva caricato i blocchi del parziale da 216.762 voci e 32.243 carte, ma
ha risposto HTTP 500 nella pubblicazione finale. La 5.2 elimina la cancellazione
ridondante dell'intero rettangolo prima della copia e può ritentare il batch
finale fino a tre volte SENZA ricaricare i blocchi. Prima di ripetere controlla
il marker finale e l'esistenza di tutti i temporanei: un batch applicato o di
esito incerto non viene ripetuto ciecamente. Un errore 500 persistente può ancora
impedire la pubblicazione; la copia locale resta disponibile.

Per FINIRE la raccolta senza ripetere 315 ricerche, carica l'intero pacchetto e
avvia Run workflow con:

- publish_only: disattivato
- partial_data: disattivato
- resume_scrape_run_id: 43737659445
- source_run_id: vuoto

Il workflow recupera il checkpoint e rilegge soltanto la ricerca interrotta e
quelle successive. Conserva i risultati già raccolti, controlla che l'elenco
espansioni sia lo stesso e completa tutte le ricerche prima di produrre Completa.
La ricerca interrotta ricomincia da pagina 1; i record sono uniti per ID.
Il checkpoint reale contiene 32.228 carte e 216.726 voci; le altre 15 carte del
parziale erano già in memoria ma non ancora scritte nel giornale e vengono
recuperate rileggendo la ricerca interrotta. I dati ripresi restano osservazioni
della precedente esecuzione, senza simulare un nuovo aggiornamento di quei prezzi.
Il foglio Info riporta il checkpoint usato.

Per importare SOLTANTO il grande parziale già esistente:

- publish_only: attivo
- partial_data: attivo
- source_run_id: 43737659445
- resume_scrape_run_id: vuoto

Il run di recupero 43745345506 è riuscito, ma ha importato il vecchio parziale
con 13.576 voci: non rappresenta il caricamento di quello da 216.762 voci.

60 test superati; verificato il checkpoint reale (315 ricerche concluse) e il
payload reale della variante senza condizione. Ripresa completa e pubblicazione
del grande dataset con la nuova versione restano da verificare sul runner.


## Versione 6.0.0 — backup progressivo in background

- Il workflow passa --live-sheets allo scraper. In locale è un'opzione esplicita.
- Un thread separato legge soltanto le righe complete del checkpoint: nessuna
  chiamata Playwright viene eseguita dal thread Google.
- Invia normalmente 50 carte nuove o aggiornate per gruppo, con TUTTE le loro
  voci Variants. Tenta l'invio anche dopo 30 secondi con un gruppo incompleto e
  svuota la coda alla conclusione o all'errore dello scraper.
- Ogni richiesta scrive in intervalli fissi, con RAW, e verifica i valori restituiti.
  Ritentare una risposta persa sovrascrive gli stessi intervalli e non fa append
  duplicati. Carte ripetute senza cambiamenti non producono nuove righe.
- Se le varianti di una carta cambiano, aggiorna le sue righe e cancella i prezzi
  superati. Possono rimanere righe vuote; i conteggi Info si riferiscono alle voci
  attive. Non vengono sommate quotazioni precedenti a quelle aggiornate.
- CGC_LIVE_Info resta IN CORSO/PARZIALE finché non sono confermate tutte le carte
  e tutte le voci del risultato locale. Un errore Google non ferma lo scraping:
  il checkpoint e l'Excel restano disponibili e il workflow prova l'importazione
  tradizionale come recupero.
- La precedente coppia CGC_LIVE_ viene conservata nascosta in
  CGC_LIVE_PRECEDENTE_. Si mantiene UNA sola generazione precedente; la più vecchia
  viene rimossa al successivo avvio. Sono schede gestite dallo script: usa altre
  schede per appunti/formule. Non vengono toccate schede senza il marcatore dello script.
- Le richieste sono distanziate e gli errori temporanei hanno tentativi limitati.
  Non è promessa una conferma Google entro 30 secondi se il servizio è lento.
- La ripresa da un vecchio checkpoint ricopia in background anche i dati già
  raccolti. La raccolta delle espansioni riparte dalla prima non completata.
  A fine scraping può essere necessario attendere che Google finisca il backup.

Sono passati 74 test: blocchi 50/50/20, invio mentre la raccolta è ancora aperta,
prezzi aggiornati, varianti aggiunte/rimosse, risposta persa, coda finale, errori
Google, ripresa e completamento coordinato di scraper e backup. Le API Google
sono simulate nei nuovi test: il backup progressivo reale va verificato sul runner.

Parametri locali:

    python scraper.py --live-sheets --live-batch-size 50 --live-flush-seconds 30

Per riprendere il run 43737659445 usa ancora resume_scrape_run_id=43737659445,
con publish_only e partial_data disattivati. Il nuovo file live_sheets.py e il
workflow aggiornato devono essere presenti nel repository.
