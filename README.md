# Spot Scout — Overnight Parking Finder

Two tools that work together:
- **`scout.py`** — queries OpenStreetMap + Gemini AI to find and score candidate parking spots
- **`spot-scout.html`** — interactive map to view, pin, and annotate spots with satellite imagery and Street View

---

## First-Time Setup

### 1. Install Python dependencies
Open Command Prompt and run:
```
py -m pip install google-genai google-cloud-aiplatform python-dotenv
```

### 2. Authenticate with Google Cloud
```
gcloud auth application-default login
```
This opens a browser — sign in with the Google account that owns the `claude-memory-202605` project. Only needs to be done once.

### 3. Verify billing
The script uses the `claude-memory-202605` GCP project which is linked to your billing account (`01A5BA-F23820-78F6F0`). GCP GenAI credits ($1,386) expire April 2027. Welcome credits ($415) expire July 2026 — use those first.

---

## Running the Scout

Open Command Prompt:
```
cd "C:\Users\Administrator\Documents\Claude\Projects\Survive Urban Jungle\ Living in car"
py scout.py "your location" --radius 2000
```

**Examples:**
```
py scout.py "Chatham-Kent, Ontario" --radius 2000
py scout.py "Windsor, Ontario" --radius 3000
py scout.py "42.4183,-82.0898" --radius 1500
```

`--radius` is in meters. 2000m (2km) is a good default. Go up to 5000 for rural areas with fewer features.

**Output:** saves a `spots_<location>.json` file in the same folder.

---

## Viewing Results in the Map

1. Open `spot-scout.html` in any browser (double-click it)
2. Click **Import** in the top bar
3. Select the `spots_<location>.json` file the scout produced
4. Spots appear as colour-coded pins:
   - 🟢 Green — good spot
   - 🟡 Yellow — okay, some risk
   - 🔴 Red — risky
   - 🔵 Blue — unknown/needs scouting
5. Click a pin → **👁 StreetView** to see ground-level view
6. Click **✏️ Edit** to add your own notes after visiting

---

## Manual Pinning

You can also drop pins manually without running the scout:
- Click anywhere on the map to place a pin
- Fill in name, rating, notes in the sidebar
- Pins are saved automatically in your browser (localStorage)
- Use **Export JSON** to back them up

---

## Troubleshooting

| Error | Fix |
|---|---|
| `ModuleNotFoundError: google.genai` | Run `py -m pip install google-genai` |
| `google.auth.exceptions.DefaultCredentialsError` | Run `gcloud auth application-default login` |
| `404 model not found` | Model name changed — update `MODEL` in scout.py to a current name |
| `429 quota exceeded` | Credits depleted — check console.cloud.google.com/billing |
| `0 OSM elements found` | Try a larger `--radius` or check the location spelling |

---

## Key Files

| File | Purpose |
|---|---|
| `scout.py` | AI-powered spot finder script |
| `spot-scout.html` | Interactive map viewer |
| `.env` | API key storage (do not share) |
| `spots_*.json` | Scout output files — import into map |

---

## GCP Project Info

- **Project:** `claude-memory-202605`
- **Model:** `gemini-2.5-flash` via Vertex AI
- **Region:** `us-central1`
- **Billing account:** `01A5BA-F23820-78F6F0` (My Billing Account)
