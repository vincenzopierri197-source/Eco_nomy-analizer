# eco-nomy – Assistente Strategico (Analisi Giuridico-Operativa)

Applicazione web a pagina singola (`index.html`) che usa le API Gemini di Google.
La chiave API Gemini **non è nel codice**: ogni utente la inserisce nel proprio browser.

© Vincenzo Pierri – Tutti i diritti riservati

## Struttura

```
index.html                 ← l'applicazione
manifest.webmanifest       ← permette "Aggiungi a schermata Home" / installazione come app
icona/
  favicon.ico, favicon-16x16.png, favicon-32x32.png, favicon-48x48.png   ← scheda del browser
  apple-touch-icon.png (180)                                              ← iPhone / iPad
  icon-192.png, icon-512.png, icon-maskable-512.png                       ← Android / app installata
  mstile-150x150.png                                                      ← riquadro Windows
  eco-nomy.ico, eco-nomy.png                                              ← collegamenti sul desktop
firebase/firestore.rules   ← regole di sicurezza da incollare in Firebase
README.md
```

## 1. Configurare Firebase (condivisione con codice)

1. https://console.firebase.google.com → **Aggiungi progetto** (senza Google Analytics).
2. **Authentication → Metodo di accesso → Anonimo → Abilita**.
3. **Firestore Database → Crea database** (modalità produzione, regione europea).
4. Scheda **Regole** → incolla il contenuto di `firebase/firestore.rules` → **Pubblica**.
5. Firestore → **TTL (Time-to-live)** → nuovo criterio: raccolta `shares`, campo `expiresAt`.
6. **Impostazioni progetto → Le tue app → Web (`</>`)** → registra l'app e copia `firebaseConfig`.
7. In `index.html` incolla `apiKey`, `authDomain`, `projectId`, `appId` nella costante `FIREBASE_CONFIG`.

## 2. Pubblicare su GitHub Pages

1. Crea un repository su GitHub e carica **tutti i file di questa cartella**.
2. **Settings → Pages → Build and deployment**: *Deploy from a branch* → branch `main`, cartella `/ (root)` → Save.
3. Dopo qualche minuto il sito è su `https://NOMEUTENTE.github.io/NOMEREPO/`.
4. In Firebase: **Authentication → Impostazioni → Domini autorizzati → Aggiungi dominio** → `NOMEUTENTE.github.io`.
5. (Consigliato) In Google Cloud Console → **API e servizi → Credenziali** → chiave API di Firebase →
   **Restrizioni applicazioni: Referrer HTTP** → `https://NOMEUTENTE.github.io/*`.
6. (Consigliato) In Google Cloud → **Fatturazione → Budget e avvisi**: imposta un avviso di spesa, per evitare abusi.

## Avvertenze

- **Mai** scrivere la chiave API Gemini nel codice o nel repository: va inserita dal campo in alto nella pagina.
- Con GitHub Free, GitHub Pages funziona solo con repository **pubblici**: il sito e il codice
  (comprese le istruzioni di sistema) sono visibili a chiunque abbia il link.
- Le conversazioni condivise sono cifrate nel browser: su Firebase c'è solo testo illeggibile
  senza il codice. Non condividere nominativi, dati identificativi o atti coperti da segreto.
- I testi inviati all'analisi vanno ai server Google (API Gemini): non caricare documenti riservati
  né atti coperti da segreto; usa solo casi pratici, generici o anonimizzati.
- Tieni la cartella `icona/` e `manifest.webmanifest` accanto a `index.html`, altrimenti le icone non si vedono.
