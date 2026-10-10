import json
import os
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore

PROJECT_ID = 'eco-nomy-analyzer'
BATCH_SIZE = 200
MAX_DELETIONS = 2000

def main():
    raw = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if not raw:
        raise RuntimeError('Secret FIREBASE_SERVICE_ACCOUNT mancante')
    account = json.loads(raw)
    if account.get('project_id') != PROJECT_ID:
        raise RuntimeError('Progetto del service account non corrispondente')
    firebase_admin.initialize_app(credentials.Certificate(account), {'projectId': PROJECT_ID})
    db = firestore.client()
    now = datetime.now(timezone.utc)
    dry_run = os.environ.get('DRY_RUN', 'true').lower() != 'false'
    total = 0
    while total < MAX_DELETIONS:
        docs = list(db.collection('shares').where('expiresAt', '<=', now).limit(min(BATCH_SIZE, MAX_DELETIONS - total)).stream())
        if not docs:
            break
        if dry_run:
            for doc in docs:
                print(f'[SIMULAZIONE] Eliminerei shares/{doc.id}')
            total += len(docs)
            break
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        total += len(docs)
        print(f'Eliminati {len(docs)} documenti; totale {total}')
    print(f"Modalità: {'SIMULAZIONE' if dry_run else 'ELIMINAZIONE'}, documenti: {total}")

if __name__ == '__main__':
    main()
