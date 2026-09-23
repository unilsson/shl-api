# shl-api

Ett litet lokalt FastAPI-baserat cache-API för SHL:s spelschema.

Syftet är att hämta hela spelschemat från SHL mer sällan och låta lokala
klienter, exempelvis Home Assistant, fråga det lokala API:t i stället för
att upprepade gånger belasta SHL:s webb-API.

## Funktioner

- Lokal JSON-cache av SHL:s spelschema
- Manuell uppdatering med `POST /refresh`
- Atomär uppdatering av cachefilen
- Validering av antalet matcher innan ny cache tas i bruk
- Nästa omgång via `/next-round`
- Valfri omgång via `/round/{round_number}`
- Närmaste matcher via `/upcoming?limit=10`
- Matcher inom ett antal dagar via `/matches?days=7`
- Status och health-endpoints
- All miljöspecifik konfiguration i lokal `.env`

## Krav

- Python 3
- pip
- venv

## Installation

Klona repot, exempelvis via SSH:

```bash
git clone git@github.com:unilsson/shl-api.git
cd shl-api
```

Skapa och aktivera en virtuell Python-miljö:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Installera beroenden:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Skapa lokal konfiguration:

```bash
cp .env.example .env
```

Redigera därefter `.env` vid behov.

Den riktiga `.env`-filen, Python-miljön och runtime-data ignoreras av Git.

## Starta utvecklingsservern

```bash
uvicorn app:app --host 0.0.0.0 --port 8093 --reload
```

## Första hämtningen

När API:t kör:

```bash
curl -s -X POST http://localhost:8093/refresh | jq
```

Det skapar den lokala cachefil som anges av `SHL_CACHE_FILE`.

## API

### Health

```text
GET /health
```

### Status

```text
GET /status
```

Visar bland annat antal matcher, antal omgångar och när cachen senast
uppdaterades.

### Matcher inom X dagar

```text
GET /matches?days=7
```

Returnerar alla ännu ospelade matcher från nu och angivet antal dagar
framåt. Urvalet görs efter faktisk matchtid och är oberoende av
omgångsnummer.

Exempel:

```bash
curl -s 'http://localhost:8093/matches?days=7' | jq
```

### Närmaste matcher

```text
GET /upcoming?limit=10
```

Returnerar de närmaste kommande matcherna.

### Nästa omgång

```text
GET /next-round
```

Returnerar den omgång som den närmaste framtida matchen tillhör.

Observera att uppskjutna eller flyttade matcher kan göra att matcher i samma
omgång ligger på vitt skilda datum. För en tittarvänlig Home Assistant-vy
är därför `/matches?days=X` normalt bättre.

### Specifik omgång

```text
GET /round/3
```

### Uppdatera cache

```text
POST /refresh
```

Hämtar ett nytt komplett spelschema från den konfigurerade SHL-källan.
Den gamla cachefilen behålls om hämtningen eller valideringen misslyckas.

## Konfiguration

Alla miljöspecifika värden finns i `.env`. Repot innehåller endast
`.env.example` som mall.

Konfigurerbara värden:

- `SHL_URL`
- `SHL_SEASON_UUID`
- `SHL_SERIES_UUID`
- `SHL_GAME_TYPE_UUID`
- `SHL_GAME_PLACE`
- `SHL_PLAYED`
- `SHL_DATA_DIR`
- `SHL_CACHE_FILE`
- `SHL_TIMEZONE`
- `SHL_MIN_GAMES`
- `SHL_REQUEST_TIMEOUT`
- `SHL_USER_AGENT`
- `API_ROOT_PATH`

## Git och lokal data

Följande ska inte versionshanteras:

- `.env`
- `.venv/`
- cachefiler under `data/`
- lokala `*.season`-dumpfiler
- loggar och temporärfiler
- temporärfiler från Emacs, Vim och vanliga IDE:er

Katalogen `data/` hålls kvar i Git med en tom `.gitkeep`.
