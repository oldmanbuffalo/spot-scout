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

**Useful flags:**

| Flag | What it does |
|---|---|
| `--verify` | After scoring, Gemini visually inspects a satellite photo of each spot (cover, sightlines, gates) and adjusts the rating. Adds `[SAT✓]` notes. ~6 extra image calls per run. |
| `--mode stealth\|convenience\|safe` | Changes scoring priorities |
| `--append` | Merge new spots into an existing JSON instead of overwriting |
| `--feedback <file>` | Feed field-tested results back into recommendations |

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
7. **🧭 Nav** on a spot card opens turn-by-turn navigation (Google Maps app on Android)

**Overlays (top bar):** 🚻 Toilets, ☕ Wi-Fi & Food, 🛒 Late Shops, 💧 Water Taps, and
**🏛 Services** — food banks, shelters, libraries (warmth/Wi-Fi/charging), gyms (showers),
places of worship. Toggle one or more, then click **🔍 Search this area**.

**GPS:** **📡 Locate** shows your live position (blue dot). **🟢 Nearest** flies to the
closest good spot from your position (falls back to 🟡, skips ✗ knocked spots).

---

## Manual Pinning

You can also drop pins manually without running the scout:
- Click anywhere on the map to place a pin
- Fill in name, rating, notes in the sidebar
- Pins are saved automatically in your browse