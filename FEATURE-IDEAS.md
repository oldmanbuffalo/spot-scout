# Spot Scout — Feature Ideas

> Generated 2026-06-11 after repo review at commit `dc755c0`.
> Current state: scout.py (OSM + Gemini scoring, modes, feedback loop, chains, lamps, 24h facilities) + spot-scout.html (Leaflet viewer, amenities overlay, field status, KML/JSON export).

---

## Recommended next 3

### 1. Satellite image verification (`scout.py --verify`)

The single biggest accuracy gain, and it directly fulfills the project's "satellite imagery" mission.

- After Gemini picks candidates from OSM data, fetch a static satellite tile for each spot (Esri World Imagery tiles are free, no key: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`)
- Send the image to `gemini-2.5-flash` (multimodal — same SDK, same credits) asking: tree cover? sightlines from road? lot size? gated?
- Adjust rating + add a `verified: true` field and a `visual_notes` field
- Cost: ~6 images/run on existing Vertex credits — negligible

### 2. Survival services layer (scout.py + HTML overlay)

OSM has the data already; one more Overpass clause and one more overlay button.

- `amenity=social_facility` + `social_facility=food_bank|shelter|soup_kitchen`
- `amenity=library` — warmth, Wi-Fi, bathrooms, outlets, daytime base
- `leisure=fitness_centre` — showers (day passes / cheap memberships)
- `amenity=place_of_worship` (many run meal programs)
- New `🏛 Services` overlay button in spot-scout.html, same pattern as the amenities overlay

### 3. "Where am I" + nearest safe spot (HTML)

Night usability when already driving.

- `navigator.geolocation.watchPosition` → blue dot on map
- One button: fly to nearest 🟢 spot from current position, show distance
- `geo:` URI link on each spot card → opens Google Maps navigation on Android

---

## Backlog (ordered by value/effort)

### scout.py

| Feature | Detail | Effort |
|---|---|---|
| Weather-aware scoring | Open-Meteo API (free, no key): overnight low, precip, wind. Cold/wet night → prefer sheltered/urban spots; add `tonight` block to JSON | Low |
| Rotation planner `--rotate 7` | Pick 7 green/yellow spots, output a night-by-night schedule that never repeats a spot on consecutive nights (avoids pattern recognition by patrols/residents) | Low |
| Noise score | Distance to nearest `railway=rail` and motorway → `noise` field; light sleeper relevance | Low |
| Residential buffer (local calc) | Compute min distance to `landuse=residential` polygons in code instead of trusting Gemini — deterministic complaint-risk score | Med |
| Sunrise/sunset in output | `astral`-free math or Open-Meteo; sets "arrive after / leave before" times per spot | Low |

### spot-scout.html

| Feature | Detail | Effort |
|---|---|---|
| Red night mode | Toggle: deep-red UI theme, dim tiles — preserves night vision, less glow through windows | Low |
| Walking line to amenity | Selected spot → straight-line + distance to nearest toilet/water/food | Low |
| Auto-backup reminder | Banner if localStorage spots haven't been exported in 7+ days | Low |
| PWA (requires hosting) | GitHub Pages on the existing repo → installable on Android, offline tiles via service worker. Note: spots data stays local in localStorage | Med |
| Spot photos | Attach phone photos to a spot (base64 in localStorage, size-capped) — remember entrances/signage | Med |

---

## Not recommended

- **Cell-coverage layer** — no good free data source; carrier maps are unreliable
- **Crowd-sourced spot sharing** — privacy risk runs opposite to the project's purpose
- **Live police scanner integration** — legality varies; low signal-to-noise

---

## Repo hygiene notes

- No `CLAUDE.md` or plan file exists in the repo — consider adding a small `CLAUDE.md` with the GCP project ID, model name, and file conventions so any session can self-orient
- Local `main` is 1 commit ahead of GitHub (`dc755c0`, the rebased 6-improvements commit) — run `git push origin main` from Windows
- `SESSION.log` is untracked; decide whether it belongs in the repo or .gitignore
