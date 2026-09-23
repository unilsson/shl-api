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
- Nästa matchdag via `/next-matchday`
- Nästa omgång via `/next-round`
- Valfri omgång via `/round/{round_number}`
- Närmaste matcher via `/upcoming?limit=10`
- Matcher inom ett antal dagar via `/matches?days=7`
- Status och health-endpoints
- Lokalt cachade lagloggor som serveras av API:t
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

### Nästa matchdag

```text
GET /next-matchday
```

Returnerar alla ännu ospelade matcher på den närmaste kalenderdagen som
har SHL-matcher. Om nästa matcher exempelvis spelas på torsdag returneras
endast torsdagens matcher, oberoende av omgångsnummer.

Detta är den rekommenderade endpointen för en Home Assistant-vy som ska visa
"vad spelas härnäst?".

Exempel:

```bash
curl -s http://localhost:8093/next-matchday | jq
```

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
är därför `/next-matchday` normalt bättre för att visa vad som spelas härnäst.

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


## Lagloggor

API:t hämtar SHL-lagens loggor direkt från Sportality-CDN med en explicit
mapping för de 14 aktuella SHL-lagen och lagrar dem lokalt under den
runtime-katalog som anges av `SHL_LOGO_DIR`.

Loggorna versionshanteras inte i Git. De blir i stället en lokal del av den
körande API-instansen och serveras under:

```text
https://api.ulnihnw.net/api/shl/logos/
```

Vid en vanlig `POST /refresh` hämtas bara loggor som saknas lokalt.
Befintliga loggor laddas alltså inte ner på nytt varje dag.

För att tvinga en kontroll/hämtning:

```bash
curl -s -X POST https://api.ulnihnw.net/api/shl/refresh-logos | jq
```

För att tvinga omladdning även av befintliga filer:

```bash
curl -s -X POST 'https://api.ulnihnw.net/api/shl/refresh-logos?force=true' | jq
```

Visa aktuella lag och deras lokala logo-URL:

```bash
curl -s https://api.ulnihnw.net/api/shl/teams | jq
```

När en logga finns lokalt innehåller matchobjekten dessutom:

```json
{
  "home": "Frölunda HC",
  "home_logo": "https://api.ulnihnw.net/api/shl/logos/frolunda-hc.svg",
  "away": "Färjestad BK",
  "away_logo": "https://api.ulnihnw.net/api/shl/logos/farjestad-bk.svg"
}
```

Det gör att Home Assistant kan använda logo-URL:erna direkt utan egen
mapping mellan lagnamn och bildfiler.


## Home Assistant

Den rekommenderade Home Assistant-integrationen använder
`/next-matchday`. API:t returnerar då alla matcher på nästa kalenderdag
med SHL-matcher, inklusive lokala URL:er till lagloggorna.

### REST-sensor

Lägg exempelvis följande i Home Assistants YAML-konfiguration:

```yaml
rest:
  - resource: "https://api.ulnihnw.net/api/shl/next-matchday"
    method: GET
    scan_interval: 300
    timeout: 10

    sensor:
      - name: "SHL nästa matchdag"
        unique_id: shl_next_matchday
        value_template: "{{ value_json.date }}"
        json_attributes:
          - count
          - matches
```

Efter ändring av YAML-konfigurationen måste berörd konfiguration laddas om,
eller Home Assistant startas om.

Sensorn får datumet för nästa matchdag som state och lagrar `count` samt
hela matchlistan i attributet `matches`.

### Markdown-kort

Följande kort visar nästa matchdag med svensk veckodag och månad, lagloggor,
matchtid, omgång, arena och ett horisontellt streck mellan matcherna:

```yaml
type: markdown
entity_id:
  - sensor.shl_nasta_matchdag

content: |-
  {% set matches = state_attr('sensor.shl_nasta_matchdag', 'matches') or [] %}
  {% set date_string = states('sensor.shl_nasta_matchdag') %}

  {% set weekdays = [
    'måndag',
    'tisdag',
    'onsdag',
    'torsdag',
    'fredag',
    'lördag',
    'söndag'
  ] %}

  {% set months = [
    'januari',
    'februari',
    'mars',
    'april',
    'maj',
    'juni',
    'juli',
    'augusti',
    'september',
    'oktober',
    'november',
    'december'
  ] %}

  {% set team_names = {
    'Djurgården Hockey Herr': 'Djurgårdens IF'
  } %}

  {% if matches | count > 0 %}
  {% set d = strptime(date_string, '%Y-%m-%d') %}

  # 🏒 SHL

  ## {{ weekdays[d.weekday()] | capitalize }} {{ d.day }} {{ months[d.month - 1] }}

  **{{ matches | count }} matcher**

  <table role="presentation" width="100%">
  {% for m in matches %}
  {% set home = team_names.get(m['home'], m['home']) %}
  {% set away = team_names.get(m['away'], m['away']) %}

  <tr>
  <td width="55" align="center">
  {% if m['home_logo'] %}
  <img src="{{ m['home_logo'] }}" width="44">
  {% endif %}
  </td>
  <td><strong>{{ home }}</strong></td>
  <td width="80" align="center"><strong>{{ m['time'] }}</strong></td>
  <td align="right"><strong>{{ away }}</strong></td>
  <td width="55" align="center">
  {% if m['away_logo'] %}
  <img src="{{ m['away_logo'] }}" width="44">
  {% endif %}
  </td>
  </tr>

  <tr>
  <td></td>
  <td colspan="3" align="center">
  <small>Omgång {{ m['round'] }}{% if m['venue'] %} · 📍 {{ m['venue'] }}{% endif %}</small>
  </td>
  <td></td>
  </tr>

  {% if not loop.last %}
  <tr>
  <td colspan="5"><hr></td>
  </tr>
  {% endif %}

  {% endfor %}
  </table>

  {% else %}

  # 🏒 SHL

  **Inga kommande SHL-matcher**

  {% endif %}
```

HTML-taggarna i tabellen ska inte indenteras ytterligare i den renderade
Markdown-texten. Fyra inledande blanksteg gör annars att Markdown tolkar
HTML-raderna som ett kodblock.

Lagloggorna hämtas från `home_logo` och `away_logo` i API-svaret och
behöver därför inte mappas separat i Home Assistant.


## Konfiguration

Alla miljöspecifika värden finns i `.env`. Repot innehåller endast
`.env.example` som mall.

Konfigurerbara värden:

- `SHL_URL`
- `SHL_LOGO_BASE_URL`
- `SHL_SEASON_UUID`
- `SHL_SERIES_UUID`
- `SHL_GAME_TYPE_UUID`
- `SHL_GAME_PLACE`
- `SHL_PLAYED`
- `SHL_DATA_DIR`
- `SHL_CACHE_FILE`
- `SHL_LOGO_DIR`
- `SHL_TIMEZONE`
- `SHL_MIN_GAMES`
- `SHL_REQUEST_TIMEOUT`
- `SHL_USER_AGENT`
- `API_ROOT_PATH`
- `PUBLIC_BASE_URL`


## Produktionsadress

API:t publiceras bakom Nginx på:

```text
https://api.ulnihnw.net/api/shl
```

Exempel:

```bash
curl -s https://api.ulnihnw.net/api/shl/health | jq
curl -s https://api.ulnihnw.net/api/shl/next-matchday | jq
```

Uvicorn kör internt på port `21967`. Nginx proxar den publika sökvägen
`/api/shl` till den lokala backend-processen.

## Köra API:t som systemd-tjänst

Repot innehåller också:

- `systemd/shl-api.service`

Den startar Uvicorn på den interna backend-porten `21967` och startar om processen automatiskt vid fel.

För nuvarande installation förutsätter unit-filen:

- användare: `unilsson`
- projektkatalog: `/opt/shl-api`
- virtualenv: `/opt/shl-api/.venv`
- intern backend-port: `21967`

Installera:

```bash
sudo cp systemd/shl-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shl-api.service
```

Kontrollera:

```bash
systemctl status shl-api.service
curl -s http://127.0.0.1:21967/health | jq
```

Loggar:

```bash
journalctl -u shl-api.service -f
```

Om projektet installeras på en annan sökväg eller under en annan användare
måste `User=`, `WorkingDirectory=` och `ExecStart=` i unit-filen
anpassas innan den kopieras till `/etc/systemd/system/`.

## Daglig uppdatering med systemd

Repot innehåller färdiga exempel under `systemd/`:

- `systemd/shl-refresh.service`
- `systemd/shl-refresh.timer`

Timern kör en cacheuppdatering varje dag klockan 04:15 och använder
`Persistent=true`, vilket innebär att en missad körning triggas efter nästa
uppstart.

När tjänsten senare installeras på en server:

```bash
sudo cp systemd/shl-refresh.service /etc/systemd/system/
sudo cp systemd/shl-refresh.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now shl-refresh.timer
```

Kontrollera timern:

```bash
systemctl list-timers shl-refresh.timer
```

Testa uppdateringen manuellt:

```bash
sudo systemctl start shl-refresh.service
systemctl status shl-refresh.service
```

Loggar visas med:

```bash
journalctl -u shl-refresh.service -n 50
```

Servicefilen förutsätter att API:t nås lokalt på
`http://127.0.0.1:21967/refresh`. Refresh-jobbet går direkt mot den lokala
Uvicorn-processen och behöver därför inte gå via Nginx, DNS eller HTTPS.

## Git och lokal data

Följande ska inte versionshanteras:

- `.env`
- `.venv/`
- cachefiler under `data/`
- lokala `*.season`-dumpfiler
- loggar och temporärfiler
- temporärfiler från Emacs, Vim och vanliga IDE:er

Katalogen `data/` hålls kvar i Git med en tom `.gitkeep`.
