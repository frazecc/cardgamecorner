# Buylist CardGameCorner su Supabase
 
Progetto dedicato: `cardgamecorner-buylist`, organizzazione `bdimmagini`.
Riferimento: `uzvvzuoorpdivanwbsen`.
URL API: `https://uzvvzuoorpdivanwbsen.supabase.co`.
Pannello: https://supabase.com/dashboard/project/uzvvzuoorpdivanwbsen/editor

## Consultazione

La vista `cgc_buylist` contiene carte, lingue, condizioni, foil, prima edizione,
prezzi buylist, credito e stato. Include l'archivio completo pubblicato.
`cgc_cards` contiene l'anagrafica; `cgc_prices` tutte le varianti originali;
`cgc_imports` i conteggi attesi e lo stato; `cgc_active` l'archivio corrente.
`cgc_sync_jobs` registra gli aggiornamenti incrementali; `cgc_sync_seen` e
`cgc_sync_changes` sono staging privato, svuotato dopo ogni commit verificato.
Le colonne `source` conservano i dati originali. Una lingua o condizione vuota
resta vuota: il sito non l'ha specificata. Uno zero non viene interpretato come
offerta gratuita: controllare sempre evaluation, excluded e status.

Le tabelle hanno RLS. Le API pubbliche possono leggere solo il risultato
pubblicato. Le scritture richiedono una chiave segreta del solo nuovo progetto.
Il progetto precedente non viene usato da questo pacchetto.

## Aggiornamenti da GitHub Actions

Caricare nel repository tutti i file del pacchetto, inclusi
`supabase_sync.py` e `.github/workflows/supabase.yml`.
In GitHub → repository → Settings → Secrets and variables → Actions creare:

* `SUPABASE_SERVICE_ROLE_KEY`: la secret key `sb_secret_...` del nuovo progetto
  oppure la sua chiave legacy `service_role`. NON la publishable/anon key.
  Si trova in Supabase → Project Settings → API Keys.

Non inserire chiavi segrete nei file, nel frontend, nei messaggi o nei log.
Il codice non legge credenziali del progetto precedente.

## Pianificazione due volte a settimana

Il workflow `.github/workflows/supabase.yml` contiene:

```yaml
schedule:
  - cron: '17 6 * * 1,4'
    timezone: Europe/Rome
```

Parte ogni **lunedi e giovedi alle 06:17, ora italiana**, anche con l'ora legale.
Le esecuzioni pianificate fanno sempre una nuova raccolta: non usano il vecchio
checkpoint. L'importazione manuale resta disponibile con `publish_only`.
Gli orari indicano l'avvio previsto; GitHub puo ritardare l'esecuzione.

Per attivarla, caricare tutti i file di questo pacchetto nel branch predefinito
`main`, incluso il workflow, e configurare il secret indicato sopra. La sola
presenza del file nel pacchetto scaricato non attiva GitHub Actions.

La prima verifica consigliata e avviare manualmente il workflow con
`publish_only=true`, `source_run_id=43749325476`: verifica credenziali e
importazione sui dati gia raccolti senza un nuovo scraping. Poi lasciare il
workflow pianificato attivo. Su repository pubblici GitHub puo disabilitare
le pianificazioni dopo 60 giorni senza attivita sul repository: controllare
Actions se gli aggiornamenti smettono di partire.

Avviare **Buylist CardGameCorner in Supabase**:

* `publish_only = true` e `source_run_id = 43749325476` per reimportare la
  raccolta completa esistente, fino alla scadenza dell'artefatto originale
  (29 novembre 2026). La ripetizione dello stesso checkpoint è idempotente.
* `publish_only = false` (predefinito) per raccogliere prezzi aggiornati e importarli.

Il nuovo workflow non carica milioni di celle in Google Sheets. La vecchia
procedura Sheets resta disponibile separatamente. Conserva Excel e checkpoint
come backup. La raccolta salva il checkpoint a ogni pagina; l'importazione
Supabase parte dopo la raccolta completa e conferma le carte a blocchi.

Se un'importazione si interrompe, la buylist pubblicata in precedenza resta
consultabile. Rilanciare lo stesso checkpoint: ogni blocco confronta le carte
con l'archivio e salva nello staging soltanto le differenze. Un import incompleto
non viene pubblicato. La pubblicazione finale confronta carte e voci prezzo,
applica le differenze ed elimina dall'archivio corrente le carte e le varianti
che non sono piu presenti nella nuova raccolta completa, in una transazione.

La versione 6.2 mantiene un solo archivio corrente, evitando una copia completa
nel database per ogni esecuzione. Lo spazio temporaneo dipende dalle carte
effettivamente cambiate; dataset in crescita, grandi variazioni e indici possono
comunque aumentare lo spazio necessario. I backup completi restano negli
artefatti GitHub per 30 giorni. Il pacchetto non cambia il piano Supabase.

## Esecuzione locale

Installare `requirements.txt`, configurare le due variabili d'ambiente
`SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`, quindi eseguire:

```sh
python -u supabase_sync.py --checkpoint CardGameCorner_Buylist_Pokemon_Completa.checkpoint.jsonl
```

`supabase_schema.sql` e `supabase_incremental.sql` documentano lo schema iniziale
e gli aggiornamenti incrementali gia installati nel nuovo progetto. Non
rieseguirli su un progetto che contiene gia queste tabelle e funzioni.
