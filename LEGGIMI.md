# EcoNomy-Analyzer: pulizia automatica delle condivisioni

## Installazione (senza modificare index.html)
1. Estrai lo ZIP e aggiungi al repository GitHub questi tre percorsi, mantenendo le cartelle: `.github/workflows/cleanup-shares.yml`, `scripts/cleanup_expired_shares.py`, `requirements-cleanup.txt`.
2. Nel progetto Google Cloud `eco-nomy-analyzer`, crea un **account di servizio dedicato** (es. `shares-cleanup`) e assegna il ruolo **Cloud Datastore User** (`roles/datastore.user`), necessario per leggere ed eliminare i documenti. Evita ruoli Owner/Editor e usa permessi più restrittivi se disponibili.
3. Crea una chiave JSON per questo account **solo se consentito dalle policy del progetto**. Non caricare MAI il JSON nel repository, nel sito o in chat. Se le chiavi JSON sono vietate, usa Workload Identity Federation con una configurazione differente.
4. Su GitHub, apri **Settings → Secrets and variables → Actions → New repository secret**. Nome: `FIREBASE_SERVICE_ACCOUNT`. Valore: l'INTERO contenuto del JSON privato.
5. Carica i tre file sul branch predefinito. In GitHub apri **Actions → Pulisci condivisioni scadute → Run workflow**. La prima volta lascia **Solo simulazione** attivato.
6. Controlla l'elenco dei documenti nei log. Quando sei sicuro, avvia manualmente il workflow deselezionando la simulazione. Da quel momento l'esecuzione pianificata avverrà ogni giorno alle **03:23 UTC** (salvo ritardi o interruzioni di GitHub).

## Sicurezza e limiti
- Lo script interviene **esclusivamente** nella raccolta `shares`, su documenti con campo Timestamp `expiresAt` minore o uguale all'ora corrente.
- Per prova mostra al massimo 200 ID; in modalità reale cancella in lotti da 200, fino a 2.000 documenti per esecuzione.
- La cancellazione è **irreversibile**; prima usa dati di prova. Non legge né stampa i contenuti cifrati `ct` o `iv`.
- Le operazioni tramite Admin SDK non rispettano le Firestore Security Rules: proteggi il secret GitHub e verifica l'accesso agli Actions. Non usare credenziali dell'app web o la chiave Gemini.
- Le letture e cancellazioni concorrono alle quote gratuite Firebase; controlla la sezione Utilizzo. GitHub Actions in repository pubblici sono normalmente gratuite; i repository privati hanno un limite di minuti inclusi.
- Le esecuzioni programmate possono ritardare o saltare; GitHub disabilita le schedule su repository pubblici inattivi per 60 giorni.
- Mantieni le regole Firestore che vietano **subito** la lettura dopo `expiresAt`: la pulizia giornaliera non garantisce eliminazione esattamente alla scadenza.
