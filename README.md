# Garden Flow

Een Flask-app voor tuinbeheer met Firebase/Firestore als primaire opslag. Het Excelbestand `professioneel_tuinbeheer_snoeiplan_verrijkt.xlsx` wordt alleen nog gebruikt als eerste importbron om de app te vullen.

## Wat de app laat zien

- Dashboard met maandselectie, KPI's en eerstvolgende taken
- Uitgebreide takenlijst met filters
- Plantregister met detailpagina per plant
- Jaarplanner met maandritme en taakdruk per plant
- Wijzigingen in taken en planten worden opgeslagen in Firestore

## Starten

1. Maak een virtuele omgeving:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Installeer Flask:

```bash
pip install -r requirements.txt
```

3. Start de app:

```bash
flask --app app run --debug
```

Open daarna `http://127.0.0.1:5000`.

## Opslag

De app ondersteunt twee backends:

- `file` voor lokale ontwikkeling en offline testen
- `firestore` voor Google Cloud / Firebase

Bij de eerste start importeert de app de planten en taken uit het Excelbestand als de gekozen backend nog leeg is.

## Deployen naar Google Cloud

Deze repo is voorbereid voor Cloud Build en Cloud Run.

- [Dockerfile](/Users/sebas/Documents/garden-management/Dockerfile) bouwt de app met `gunicorn`
- [cloudbuild.yaml](/Users/sebas/Documents/garden-management/cloudbuild.yaml) bouwt en deployed naar Cloud Run
- [app.py](/Users/sebas/Documents/garden-management/app.py) ondersteunt Firestore als productie-opslag

Belangrijk:

- Voor Cloud Run gebruikt de deploy `GARDEN_DATA_BACKEND=firestore`.
- Je moet in Google Cloud eerst Firestore in Native mode activeren in hetzelfde project.
- De app gebruikt standaard de collecties `${prefix}_plants` en `${prefix}_tasks`, waarbij `prefix` uit `FIRESTORE_COLLECTION_PREFIX` komt.
- De Cloud Build deploy target is `garden-manager` in `europe-west1` en forceert `ingress=all`, `allow-unauthenticated` en de runtime service account.

## Cloud Run trigger

Als je repo via Google Cloud is gekoppeld, controleer dan dat de trigger echt [cloudbuild.yaml](/Users/sebas/Documents/garden-management/cloudbuild.yaml) gebruikt.

Als de trigger de standaard "source deploy" flow gebruikt in plaats van dit bestand, dan kunnen instellingen zoals:

- service name
- ingress
- service account
- environment variables

weer overschreven worden.

Voor lokaal ontwikkelen zonder Firestore:

```bash
export GARDEN_DATA_BACKEND=file
flask --app app run --debug
```
