# Collegamento a un Google Sheet esistente

Lo scraper continua a raccogliere tutta la buylist Pokémon con tutte le voci
Variants di ogni carta. Al termine di una scansione COMPLETA il workflow importa
automaticamente i dati nel tuo documento Google Sheets.

La connessione di Google Drive a ChatGPT non è necessaria per questa modalità:
lo script in GitHub Actions si autentica direttamente alla Google Sheets API.
Serve una configurazione iniziale nel tuo account Google Cloud e nel repository.

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
   New repository secret e crea queste DUE voci:

| Nome Secret | Contenuto |
| --- | --- |
| GOOGLE_SHEET_ID | Il link del Google Sheet oppure l'ID tra /d/ e /edit |
| GOOGLE_SERVICE_ACCOUNT_JSON | Tutto il contenuto del JSON scaricato al punto 4 |

Il JSON contiene una chiave privata: inseriscilo solo nel Secret, senza caricarlo
nel repository pubblico o incollarlo nella chat. Non serve inviarlo a me.

7. Aggiorna tutti i file dello ZIP nel repository mantenendo le cartelle:
   scraper.py, sheets_sync.py, requirements.txt, tests/ e
   .github/workflows/scrape.yml. Le guide sono facoltative per l'esecuzione.
8. In Actions > Estrai Buylist CardGameCorner > Run workflow lascia
   publish_only disattivato e avvia sul branch main.

Il workflow controlla subito che le credenziali permettano di leggere il documento,
prima della lunga scansione. La prova iniziale è di lettura; al service account
va assegnato il ruolo Editor al punto 5 per consentire la scrittura finale.

Se entrambi i Secrets sono assenti lo scraper produce soltanto Excel. Se ne è
impostato solo uno, il controllo si interrompe invece di saltare silenziosamente
l'importazione richiesta.

## Come viene aggiornato il documento

Crea e gestisce quattro schede:

- CGC_Prezzi: tutte le varianti, lingue, condizioni, foil e prime edizioni;
  prezzi buylist e crediti numerici separati; inclusi prezzi zero, esclusioni
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
la pubblicazione Google fallisce. Il file PARZIALE non viene pubblicato come
completo. Dati_originali e il JSONL restano negli artifact per evitare di occupare
inutilmente celle Google. Nessun prezzo o lingua viene escluso da CGC_Prezzi.

## Ripetere SOLO l'importazione senza rifare la scansione

Se la scansione era riuscita ma Sheets non era configurato o il trasferimento è
fallito, apri Run workflow, abilita publish_only e scrivi source_run_id: il numero
che trovi nell'URL /actions/runs/NUMERO dell'esecuzione con un Excel completo.

Il workflow scarica l'artifact Buylist-Pokemon-Excel di quel run e lo importa,
senza avviare Chromium o visitare di nuovo CardGameCorner. Funziona finché
l'artifact è disponibile. L'Excel deve provenire dalla versione 4.0 o successiva
ed essere completo, con tutti i conteggi coerenti.

## Uso locale

    python -m pip install -r requirements.txt
    python -m playwright install chromium
    python scraper.py

Imposta GOOGLE_SHEET_ID e GOOGLE_APPLICATION_CREDENTIALS (percorso al JSON)
nel tuo ambiente, poi esegui:

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
15 minuti e su errore, al posto di una riscrittura ogni dieci espansioni. È una
copia diagnostica, non un sistema di ripresa automatica dello scraping.
Per cambiare l'intervallo usa --partial-interval 300; 0 scrive Excel solo su
errore e alla fine. Per scaricare anche le immagini usa --load-images.

Non è promesso un fattore di accelerazione: i tempi effettivi dipendono dal sito
ed è ancora necessaria una scansione completa della nuova versione sul runner.
L'importazione reale nel tuo Google Sheet resta da attivare con i due Secrets.
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
