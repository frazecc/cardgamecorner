# Google Sheets — backup progressivo della versione 6

La destinazione IMPORT CGC e le credenziali già configurate restano valide.
Con il nuovo workflow la raccolta viene salvata durante lo scraping in:

- CGC_LIVE_Prezzi: prezzi di tutte le lingue, condizioni e varianti raccolte;
- CGC_LIVE_Info: stato IN CORSO, PARZIALE oppure COMPLETO e conteggi confermati.

Normalmente vengono inviate 50 carte per blocco, con tutte le loro varianti.
Il thread tenta l'invio anche dopo 30 secondi con un blocco incompleto. Le
richieste rispettano la pausa configurata e vengono verificate confrontando
l'eco dei valori del server, senza aggiungere una richiesta di lettura per carta.

Il checkpoint locale è scritto e sincronizzato su disco dopo ogni pagina.
Se Google si interrompe, lo scraper continua a raccogliere dati. Se la raccolta
si ferma, il thread prova a svuotare la coda e indica PARZIALE. Solo quando tutti
i conteggi coincidono con il risultato locale dichiara COMPLETO.

Con backup live riuscito non serve la vecchia copia finale di milioni di celle:
il risultato aggiornato è già in CGC_LIVE_Prezzi. Il file Excel completo continua
ad essere caricato negli artifact. Se il live fallisce, il workflow tenta la
precedente importazione da Excel come recupero. publish_only resta disponibile
per recuperare vecchi file nelle schede CGC_ o CGC_PARZIALE_.

Ad ogni nuovo run viene creata una nuova coppia CGC_LIVE_. La coppia precedente
viene nascosta come CGC_LIVE_PRECEDENTE_; la copia ancora più vecchia viene
eliminata. Si conserva quindi una sola generazione precedente. I link gid e le
formule che puntano alla vecchia coppia continueranno a puntare alla copia
precedente: usa la coppia CGC_LIVE_ visibile per i dati correnti. Non mettere
appunti o formule manuali nelle schede gestite dallo script.

Aggiorna anche live_sheets.py e .github/workflows/scrape.yml. Non servono nuove
chiavi o permessi Google se l'account di servizio ha già il ruolo Editor.

Per riprendere la raccolta precedente: resume_scrape_run_id=43737659445,
publish_only=false, partial_data=false. I dati del checkpoint vengono copiati
in background mentre lo scraper completa le espansioni mancanti. Il backup
può restare indietro rispetto alla raccolta e viene atteso prima della fine.

74 test superati con API simulate; l'esecuzione live della nuova modalità è
ancora da verificare. Una quota esaurita o un errore persistente del servizio
può impedire il backup remoto: la copia locale non viene scartata.

---

# Collegamento a un Google Sheet esistente

Lo scraper continua a raccogliere tutta la buylist Pokémon con tutte le voci
Variants di ogni carta. Al termine di una scansione COMPLETA il workflow importa
automaticamente i dati nel tuo documento Google Sheets. In caso di errore tenta
anche l’importazione dei dati raccolti in schede CGC_PARZIALE_ separate.

La connessione di Google Drive a ChatGPT non è necessaria per questa modalità:
lo script in GitHub Actions si autentica direttamente alla Google Sheets API.
Il documento IMPORT CGC è già impostato come destinazione:

    1SH8_HmCd5YV37woGIZUwemy_GLh6Y9Z_28OQf_B_Rlw

Il link è stato aperto e il titolo verificato nel browser. Questo non verifica
l'accesso del futuro account di servizio: servono ancora credenziali e condivisione.
La configurazione iniziale rimane necessaria nel tuo account Google Cloud e nel repository.

## Configurazione una tantum

1. Apri https://console.cloud.google.com/ e seleziona o crea un progetto.
2. In API e servizi > Libreria abilita Google Sheets API.
3. In IAM e amministrazione > Account di servizio crea un account di servizio
   dedicato, per esempio cardgamecorner-sheets. Non servono ruoli amministrativi
   sul progetto e non serve delega a livello di dominio.
4. Apri l'account di servizio > Chiavi > Aggiungi chiave > Crea nuova chiave > JSON.
   Scarica il file. Se la tua organizzazione impedisce di creare chiavi, usa
   credenziali ADC/federazione; non disattivare il criterio dell'organizzazione.
5. Nel JSON individua client_email. Apri il Google Sheet già esistente, fai
   Condividi, aggiungi quell'indirizzo come Editor e disattiva la notifica.
   Il foglio non deve essere pubblico. Il link da solo non concede accesso.
6. Nel repository GitHub apri Settings > Secrets and variables > Actions >
   New repository secret e crea questa voce:

| Nome Secret | Contenuto |
| --- | --- |
| GOOGLE_SERVICE_ACCOUNT_JSON | Tutto il contenuto del JSON scaricato al punto 4 |

GOOGLE_SHEET_ID non serve per IMPORT CGC: il suo ID è già configurato.
Se in futuro vuoi usare un altro documento, puoi aggiungere il Secret opzionale
GOOGLE_SHEET_ID con il suo URL/ID: questo prevale sulla destinazione preconfigurata.

Il JSON contiene una chiave privata: inseriscilo solo nel Secret, senza caricarlo
nel repository pubblico o incollarlo nella chat. Non serve inviarlo a me.

7. Aggiorna tutti i file dello ZIP nel repository mantenendo le cartelle:
   scraper.py, sheets_sync.py, live_sheets.py, requirements.txt, tests/ e
   .github/workflows/scrape.yml. Le guide sono facoltative per l'esecuzione.
8. In Actions > Estrai Buylist CardGameCorner > Run workflow lascia
   publish_only disattivato e avvia sul branch main.

Il workflow controlla subito che le credenziali permettano di leggere il documento,
prima della lunga scansione. La prova iniziale è di lettura; al service account
va assegnato il ruolo Editor al punto 5 per consentire la scrittura finale.

Con questa configurazione l'importazione Google è attiva nel workflow. Se le
credenziali non sono configurate, il controllo iniziale si ferma prima della lunga
scansione. Per produrre soltanto Excel in locale usa python scraper.py.

## Come viene aggiornato il documento

Crea e gestisce quattro schede:

- CGC_Prezzi: tutte le varianti, lingue, condizioni, foil e prime edizioni;
  prezzi buylist e crediti numerici separati; Chiave voce univoca e segnalazione
  degli ID del sito ripetuti; inclusi prezzi zero, esclusioni
  e valutazioni su richiesta.
- CGC_Carte: anagrafica e conteggi delle varianti.
- CGC_Info: fonte, copertura, conteggi e data dell'importazione.
- CGC_Espansioni: ricerche completate.

Le altre schede del documento restano come sono. Se una scheda con un nome CGC_
è già presente ma non è stata creata da questo importatore, lo script si ferma:
rinominala se vuoi conservare i dati e lasciare quel nome all'importatore.
Le quattro schede gestite vengono sostituite ad ogni importazione: usa le altre
schede per appunti o formule personali.

I dati vengono scritti in blocchi fino a 2.500 righe e circa 1,5 MB UTF-8, con
RAW per conservare ID e testo. Ogni blocco viene riletto e confrontato. Poi un
unico batch atomico aggiorna le schede visibili mantenendo i loro sheetId/gid:
le formule nelle altre schede continuano a puntare agli stessi fogli.

Se il caricamento o la verifica si interrompe, i dati visibili precedenti restano
validi. Le schede temporanee nascoste di quella importazione vengono rimosse
quando la connessione lo consente. Una risposta persa dopo la pubblicazione viene
verificata tramite un ID di importazione, evitando di duplicare le righe.

L'Excel completo rimane disponibile nell'artifact Buylist-Pokemon-Excel anche se
la pubblicazione Google fallisce. Se lo scraper si interrompe dopo avere raccolto dei dati, il workflow tenta
anche l'importazione del file PARZIALE in quattro schede CGC_PARZIALE_ separate.
Non sostituisce il risultato completo nelle schede CGC_. Lo stato PARZIALE e
l'errore restano nel relativo foglio Info. Dati_originali e il JSONL restano negli artifact per evitare di occupare
inutilmente celle Google. Nessun prezzo o lingua viene escluso da CGC_Prezzi.

## Ripetere SOLO l'importazione senza rifare la scansione

Se la scansione era riuscita ma Sheets non era configurato o il trasferimento è
fallito, apri Run workflow, abilita publish_only e scrivi source_run_id: il numero
che trovi nell'URL /actions/runs/NUMERO dell'esecuzione con un Excel completo.

Il workflow scarica l'artifact Buylist-Pokemon-Excel di quel run e lo importa,
senza avviare Chromium o visitare di nuovo CardGameCorner. Funziona finché
l'artifact è disponibile. L'Excel deve provenire dalla versione 4.0 o successiva
con tutti i conteggi coerenti. Per recuperare un PARZIALE abilita anche
partial_data: il workflow scarica Buylist-Pokemon-Diagnostica invece dell'Excel
completo e scrive nelle schede CGC_PARZIALE_.

Per recuperare il grande parziale del run 43737659445 usa publish_only=true,
partial_data=true, source_run_id=43737659445. Sono disponibili 32.243 carte e
216.762 voci prezzo nel parziale: non è ancora la buylist completa.
Per FINIRE la raccolta usa invece resume_scrape_run_id=43737659445 e lascia
publish_only disattivato.

## Uso locale

    python -m pip install -r requirements.txt
    python -m playwright install chromium
    python scraper.py

Imposta GOOGLE_APPLICATION_CREDENTIALS (percorso al JSON) nel tuo ambiente,
poi esegui (IMPORT CGC è già selezionato):

    python sheets_sync.py --check
    python sheets_sync.py

Se l'Excel completo è già disponibile, esegui soltanto sheets_sync.py.
Le credenziali ADC sono supportate per chi ha già un'autenticazione Google
configurata; l'accesso al documento deve essere concesso a quella identità.

## Velocità e controlli

La versione 5 evita il download di immagini/font/video (i link delle immagini
restano esportati), controlla il caricamento con dati leggeri e legge il JSON
completo delle nuove carte una sola volta per pagina. Anche se Vedi altre carte
mantiene tutte le carte precedenti nel DOM, queste non vengono rielaborate.

Il JSONL viene aggiornato dopo ogni pagina. L'Excel parziale viene scritto ogni
15 minuti e su errore, al posto di una riscrittura ogni dieci espansioni.
Dalla 5.2 il giornale permette anche la ripresa esplicita: in Run workflow usa
resume_scrape_run_id con l'ID del run interrotto e lascia publish_only spento.
Solo le ricerche concluse vengono saltate; quella interrotta riparte da pagina 1.
Per ripartire da capo lascia il campo vuoto.
Per cambiare l'intervallo usa --partial-interval 300; 0 scrive Excel solo su
errore e alla fine. Per scaricare anche le immagini usa --load-images.

Non è promesso un fattore di accelerazione: i tempi effettivi dipendono dal sito
ed è ancora necessaria una scansione completa della nuova versione sul runner.
L'importazione reale nel tuo Google Sheet resta da attivare con il Secret delle credenziali e la condivisione con il service account.
I test del trasferimento usano un modello API e non credenziali del tuo account.

## Fonti tecniche

- Autenticazione e condivisione diretta con account di servizio:
  https://developers.google.com/workspace/guides/create-credentials
- API, batch atomici e quote:
  https://developers.google.com/workspace/sheets/api/limits
- RAW conserva testo e identificatori senza interpretarli:
  https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption
- Limiti del documento Google Sheets:
  https://support.google.com/drive/answer/37603


## Errori temporanei nella pubblicazione finale

La versione 5.2 controlla il marker e i temporanei dopo un HTTP 500/502/503/504,
429 o una risposta persa. Se è provato che il batch finale non è stato applicato,
può ripeterlo fino a tre volte usando i blocchi già caricati. Gli ID delle schede
visibili vengono conservati. La cancellazione completa prima della copia è stata
rimossa, perché il rettangolo viene già sovrascritto dalla copia stessa.

La guida Google avverte che grandi aggiornamenti batch possono andare in timeout
ed indica le riprove con backoff per gli errori temporanei:
https://developers.google.com/workspace/sheets/api/troubleshoot-api-errors

Per il run interrotto dopo 315 ricerche usa resume_scrape_run_id=43737659445.
Per importarne solo il parziale usa invece publish_only=true, partial_data=true,
source_run_id=43737659445. Non attivare insieme publish_only e la ripresa.
