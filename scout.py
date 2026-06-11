"""
spot_scout.py — Overnight parking spot finder
Queries OpenStreetMap for candidate locations, scores them with Gemini via google-genai SDK,
outputs results as JSON importable into spot-scout.html

Auth: uses application default credentials (run `gcloud auth application-default login` once)
Project: claude-memory-202605
"""

import os, json, time, math, argparse, urllib.request, urllib.parse, urllib.error
from datetime import datetime

# ── Gemini setup (new google-genai SDK) ───────────────────────────────────────
try:
    from google import genai
    from google.genai import types
except ImportError:
    raise SystemExit("ERROR: Run `py -m pip install google-genai` first")

GCP_PROJECT = "claude-memory-202605"
GCP_LOCATION = "us-central1"
MODEL = "gemini-2.5-flash"

client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)

# ── Overpass query ────────────────────────────────────────────────────────────
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Retailers/chains with documented or well-known overnight parking tolerance
OVERNIGHT_CHAINS = {
    "walmart":        ("Walmart",              "explicitly allows overnight parking by policy"),
    "wal-mart":       ("Walmart",              "explicitly allows overnight parking by policy"),
    "cracker barrel": ("Cracker Barrel",       "explicitly welcomes RV/car overnight parking"),
    "flying j":       ("Flying J",             "truck stop — overnight parking standard"),
    "pilot":          ("Pilot Travel Center",  "truck stop — overnight parking standard"),
    "loves":          ("Love's Travel Stop",   "truck stop — overnight parking standard"),
    "love's":         ("Love's Travel Stop",   "truck stop — overnight parking standard"),
    "cabela":         ("Cabela's",             "large lot, commonly allows overnight stays"),
    "bass pro":       ("Bass Pro Shops",       "large lot, commonly allows overnight stays"),
    "camping world":  ("Camping World",        "RV-focused, overnight usually fine"),
    "sam's club":     ("Sam's Club",           "some locations allow overnight parking"),
    "costco":         ("Costco",               "some locations allow overnight parking"),
    "menards":        ("Menards",              "some locations allow overnight parking"),
}

def build_overpass_query(lat, lon, radius_m=2000):
    """
    Pull features useful for overnight parking scouting:
    - Roads: service, track, unclassified, residential, tertiary, forestry
    - Parking: nodes, areas, lay-bys, rest areas, truck stops
    - Landuse: industrial, commercial, retail, farmyard, forest, recreation
    - Natural: wood, scrub, grassland, water (reference), heath
    - Leisure: marina, boat launch, nature reserve, campsite, golf (quiet at night)
    - Tourism: camp_site, caravan_site, picnic_site, viewpoint (legal overnight common)
    - Amenities: fuel (24h), hospital, police (proximity awareness)
    - Highways: rest_area, services, bus_stop (lay-bys near these)
    - Boundaries: conservation areas, provincial/national parks
    """
    bb = f"(around:{radius_m},{lat},{lon})"
    return f"""
[out:json][timeout:60][maxsize:16000000];
(
  way["highway"~"^(service|track|unclassified|tertiary|road)$"]{bb};
  nwr["amenity"~"^(parking|rest_area|truck_stop|fuel|police|hospital|fast_food|toilets|drinking_water)$"]{bb};
  nwr["amenity"~"^(social_facility|library|place_of_worship)$"]{bb};
  nwr["leisure"="fitness_centre"]{bb};
  nwr["shop"~"^(convenience|supermarket)$"]{bb};
  nwr["landuse"~"^(industrial|commercial|retail|forest|farmyard)$"]{bb};
  nwr["natural"~"^(wood|scrub|grassland|heath)$"]{bb};
  nwr["tourism"~"^(camp_site|caravan_site|picnic_site|viewpoint)$"]{bb};
  nwr["leisure"~"^(marina|slipway|nature_reserve|park)$"]{bb};
  node["highway"="street_lamp"]{bb};
);
out body center qt;
"""

def query_overpass(lat, lon, radius_m=2000):
    query = build_overpass_query(lat, lon, radius_m)
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data,
                                  headers={"User-Agent": "SpotScout/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        raise SystemExit(f"Overpass query failed: {e}")

# ── Summarise OSM data for Gemini ─────────────────────────────────────────────
def summarise_osm(osm_data, center_lat, center_lon):
    elements = osm_data.get("elements", [])

    roads, parking, landuse, natural, amenities, tourism, leisure, facilities, lamps = [], [], [], [], [], [], [], [], []
    chain_spots, services = [], []

    for el in elements:
        tags = el.get("tags", {})
        etype = el.get("type")

        # Get representative coordinate
        if etype == "node":
            clat, clon = el.get("lat"), el.get("lon")
        elif etype == "way":
            c = el.get("center", {})
            clat, clon = c.get("lat"), c.get("lon")
        else:
            continue

        if not clat or not clon:
            continue

        dist = haversine(center_lat, center_lon, clat, clon)
        name = tags.get("name", "")
        hw       = tags.get("highway", "")
        lu       = tags.get("landuse", "")
        nat      = tags.get("natural", "")
        amenity  = tags.get("amenity", "")
        tourism_tag = tags.get("tourism", "")
        leisure_tag = tags.get("leisure", "")
        access   = tags.get("access", "unknown")
        boundary = tags.get("boundary", "")
        waterway = tags.get("waterway", "")

        if hw in ("service","track","unclassified","residential","tertiary","road") or \
           hw in ("rest_area","services"):
            roads.append({
                "dist_m": int(dist), "type": hw, "name": name,
                "lat": clat, "lon": clon,
                "access": access,
                "tracktype": tags.get("tracktype",""),
                "surface": tags.get("surface","unknown")
            })
        elif amenity in ("parking","parking_space","rest_area","truck_stop"):
            parking.append({
                "dist_m": int(dist), "type": amenity, "name": name,
                "lat": clat, "lon": clon,
                "access": access,
                "fee": tags.get("fee","unknown"),
                "maxstay": tags.get("maxstay","unknown"),
                "opening_hours": tags.get("opening_hours","unknown")
            })
        elif amenity in ("social_facility", "library", "place_of_worship") or \
             leisure_tag == "fitness_centre":
            stype = "fitness_centre" if leisure_tag == "fitness_centre" else amenity
            services.append({
                "dist_m": int(dist), "type": stype, "name": name,
                "lat": clat, "lon": clon,
                "detail": tags.get("social_facility", ""),
                "opening_hours": tags.get("opening_hours", "unknown")
            })
        elif lu:
            landuse.append({"dist_m": int(dist), "type": lu, "name": name,
                            "lat": clat, "lon": clon})
        elif nat:
            natural.append({"dist_m": int(dist), "type": nat, "name": name,
                            "lat": clat, "lon": clon})
        elif tourism_tag:
            tourism.append({
                "dist_m": int(dist), "type": tourism_tag, "name": name,
                "lat": clat, "lon": clon,
                "access": access,
                "fee": tags.get("fee","unknown")
            })
        elif leisure_tag:
            leisure.append({"dist_m": int(dist), "type": leisure_tag, "name": name,
                            "lat": clat, "lon": clon, "access": access})
        elif amenity in ("fuel","hospital","police","bus_station"):
            amenities.append({"dist_m": int(dist), "type": amenity, "name": name,
                              "lat": clat, "lon": clon})
        elif amenity in ("fast_food","toilets","drinking_water") or \
             tags.get("shop","") in ("convenience","supermarket"):
            ftype = amenity or tags.get("shop","")
            facilities.append({
                "dist_m": int(dist), "type": ftype, "name": name,
                "lat": clat, "lon": clon,
                "opening_hours": tags.get("opening_hours","unknown")
            })
        elif boundary in ("protected_area","national_park","provincial_park"):
            natural.append({"dist_m": int(dist), "type": boundary, "name": name,
                            "lat": clat, "lon": clon})
        elif waterway in ("boat_ramp","slipway"):
            leisure.append({"dist_m": int(dist), "type": waterway, "name": name,
                            "lat": clat, "lon": clon, "access": access})
        elif hw == "street_lamp":
            lamps.append({"lat": clat, "lon": clon, "dist_m": int(dist)})

        # Chain-store name match (runs for every element regardless of above)
        name_lc = name.lower()
        for key, (chain_name, policy) in OVERNIGHT_CHAINS.items():
            if key in name_lc:
                chain_spots.append({
                    "dist_m": int(dist), "chain": chain_name,
                    "policy": policy, "lat": clat, "lon": clon
                })
                break

    # Sort by distance, cap lists to keep prompt size manageable
    roads    = sorted(roads,    key=lambda x: x["dist_m"])[:35]
    parking  = sorted(parking,  key=lambda x: x["dist_m"])[:20]
    landuse  = sorted(landuse,  key=lambda x: x["dist_m"])[:20]
    natural  = sorted(natural,  key=lambda x: x["dist_m"])[:15]
    tourism  = sorted(tourism,  key=lambda x: x["dist_m"])[:15]
    leisure  = sorted(leisure,  key=lambda x: x["dist_m"])[:15]
    amenities= sorted(amenities,key=lambda x: x["dist_m"])[:10]
    facilities= sorted(facilities,key=lambda x: x["dist_m"])[:15]
    lamps      = sorted(lamps,      key=lambda x: x["dist_m"])[:100]
    chain_spots= sorted(chain_spots,key=lambda x: x["dist_m"])
    services   = sorted(services,   key=lambda x: x["dist_m"])[:15]

    return {
        "center": {"lat": center_lat, "lon": center_lon},
        "roads": roads,
        "parking_nodes": parking,
        "landuse_zones": landuse,
        "natural_cover": natural,
        "tourism_sites": tourism,
        "leisure_areas": leisure,
        "nearby_amenities": amenities,
        "facilities_24h": facilities,
        "street_lamps": lamps,
        "overnight_chains": chain_spots,
        "support_services": services,
        "total_elements": len(elements)
    }

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2-lat1)*p/2)**2 +
         math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2)
    return 2*R*math.asin(math.sqrt(a))

# ── Gemini analysis ───────────────────────────────────────────────────────────
MODE_PREFIXES = {
    "stealth":     "PRIORITY MODE — STEALTH: rank concealment above all. Prefer deep cover, unpaved tracks, forest edges, industrial dead-ends. Deprioritize facilities distance.",
    "convenience": "PRIORITY MODE — CONVENIENCE: rank proximity to 24h facilities (fuel, fast food, toilets, water) above stealth. Accept visible spots if bathroom/water is walking distance.",
    "safe":        "PRIORITY MODE — SAFETY: rank ambient light and nearby 24h human activity above stealth. Avoid complete isolation. Some visibility is acceptable for security.",
    "default":     "",
}

SYSTEM_PROMPT = """You are an expert at finding discreet overnight vehicle parking spots for someone living in their car.
Given OpenStreetMap geographic data around a location, identify the best candidate spots.

For each spot consider:
- STEALTH: low visibility from main roads, residential areas, or police patrol routes
- SAFETY: not isolated in a dangerous area, some ambient light or activity nearby
- PRACTICALITY: flat enough to sleep, no time restrictions, legal or low-enforcement risk
- COVER: trees, buildings, terrain that shields the vehicle
- PROXIMITY: distance from the search center
- FACILITIES: proximity to 24h fuel stations, fast food, toilets, or convenience stores
  (critical for car living — bathroom access, water, charging)
- LIGHTING: use the street_lamps list — high lamp density within 50m = high nighttime exposure;
  prefer spots where the nearest lamp cluster is 100m+ away
- CHAINS: overnight_chains in the data are explicitly overnight-friendly lots;
  always recommend any within range as top candidates
- SERVICES: support_services lists libraries (daytime base: warmth, Wi-Fi, bathroom,
  charging), fitness centres (showers), social facilities (food banks, shelters, meals),
  and places of worship (many run meal programs). Spots within easy reach of these
  get a practicality bonus; mention the nearest relevant service in the notes.

Good spot types: industrial/commercial lots at night, forest service roads, church parking lots,
dead-end service roads, large retail back lots, rest areas, truck stops, campgrounds.
Priority bonus: spots within 1km of a 24h fuel station or fast food (bathroom + water access).
TOP PRIORITY: any overnight_chains entry — these are gold-standard spots; list them first.

Bad spot types: residential streets (complaints), high-foot-traffic areas, anywhere with
clearly posted no overnight parking, near police stations.

Return ONLY a valid JSON array of up to 6 spots, no markdown, no explanation outside the JSON.
Each spot must have exactly these fields:
{
  "id": "spot_<unix_timestamp>_<index>",
  "name": "short descriptive name",
  "lat": <float>,
  "lon": <float>,
  "rating": one of "🟢" (good) | "🟡" (okay) | "🔴" (risky),
  "notes": "2-3 sentence assessment: why this spot, risks, best time to arrive",
  "created": "<ISO datetime string>"
}"""

def _build_feedback_block(feedback):
    if not feedback:
        return ""
    knocked = [s for s in feedback if s.get("fieldStatus") == "knocked"]
    visited = [s for s in feedback if s.get("fieldStatus") == "visited"]
    lines = []
    if knocked:
        lines.append("PREVIOUSLY REJECTED SPOTS — avoid similar terrain/context:")
        for s in knocked[:10]:
            lng = s.get("lng", s.get("lon", 0))
            lines.append(f"  ✗ {s.get('name','?')} ({s['lat']:.5f},{lng:.5f}): {s.get('notes','')[:120]}")
    if visited:
        lines.append("PREVIOUSLY SUCCESSFUL SPOTS — similar contexts are good:")
        for s in visited[:6]:
            lng = s.get("lng", s.get("lon", 0))
            lines.append(f"  ✓ {s.get('name','?')} ({s['lat']:.5f},{lng:.5f})")
    return ("\n".join(lines) + "\n") if lines else ""

def ask_gemini(summary, mode="default", feedback=None):
    mode_line = MODE_PREFIXES.get(mode, "")
    feedback_block = _build_feedback_block(feedback)
    prompt = f"""Analyze this OpenStreetMap data and identify the best overnight parking spots.
{mode_line}
{feedback_block}
Data:
{json.dumps(summary, indent=2)}

Return a JSON array of up to 6 candidate spots as specified."""

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=8192,
                response_mime_type="application/json"
            )
        )
        text = response.text.strip()
    except Exception as e:
        raise SystemExit(f"Gemini request failed: {e}")

    try:
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rstrip("`").strip()

        # Attempt to repair truncated JSON by closing open array
        try:
            spots = json.loads(text)
        except json.JSONDecodeError:
            # Find last complete object (ending with }) and close the array
            last_close = text.rfind("}")
            if last_close != -1:
                text = text[:last_close+1] + "\n]"
                spots = json.loads(text)
            else:
                raise

        if not isinstance(spots, list):
            spots = [spots]

        # Ensure IDs are unique and fields are normalised
        ts = int(time.time())
        for i, s in enumerate(spots):
            s["id"] = f"spot_{ts}_{i}"
            s.setdefault("created", datetime.utcnow().isoformat() + "Z")
            s.setdefault("rating", "🔵")
            s.setdefault("notes", "")
            s.setdefault("name", f"Spot {i+1}")
            # Normalise lon → lng for spot-scout.html
            if "lon" in s and "lng" not in s:
                s["lng"] = s.pop("lon")
        return spots
    except Exception as e:
        raise SystemExit(f"Failed to parse Gemini response: {e}\nRaw text: {text}")

# ── Satellite image verification ──────────────────────────────────────────────
SAT_EXPORT_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"

VERIFY_PROMPT = """You are visually verifying a candidate overnight parking spot for someone
living in their car. The attached satellite photo is centered on the candidate spot
(roughly {span}m across).

Assess from the imagery only:
- COVER: tree canopy, buildings, or terrain that would shield a parked car
- SIGHTLINES: is the spot visible from main roads or houses?
- SURFACE: paved lot, gravel, grass, or undrivable?
- ACCESS: visible gates, barriers, fences, or a clear entrance?
- CONTEXT: residential windows overlooking it? commercial/industrial? isolated?

Prior assessment of this spot: {prior_rating} — {prior_notes}

Return ONLY valid JSON, no markdown:
{{
  "verdict": "confirm" | "upgrade" | "downgrade",
  "rating": "🟢" | "🟡" | "🔴",
  "visual_notes": "2 sentences max: what the imagery shows that matters"
}}"""

def fetch_sat_image(lat, lon, span_m=250, size=512):
    """Fetch a satellite image centered on (lat, lon) from Esri World Imagery (free, no key)."""
    dlat = span_m / 2 / 111320
    dlon = span_m / 2 / (111320 * math.cos(math.radians(lat)))
    params = urllib.parse.urlencode({
        "bbox": f"{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}",
        "bboxSR": "4326", "imageSR": "3857",
        "size": f"{size},{size}", "format": "jpg", "f": "image"
    })
    req = urllib.request.Request(f"{SAT_EXPORT_URL}?{params}",
                                  headers={"User-Agent": "SpotScout/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def verify_spots(spots, span_m=250):
    """Visually verify each spot against satellite imagery via Gemini multimodal."""
    print("\nVerifying spots against satellite imagery...")
    for i, s in enumerate(spots, 1):
        lat, lng = s["lat"], s.get("lng", s.get("lon"))
        try:
            img = fetch_sat_image(lat, lng, span_m=span_m)
        except Exception as e:
            print(f"  {i}. {s.get('name','?')}: image fetch failed ({e}) — skipped")
            continue
        prompt = VERIFY_PROMPT.format(span=span_m,
                                      prior_rating=s.get("rating", "🔵"),
                                      prior_notes=s.get("notes", "")[:200])
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt],
                config=types.GenerateContentConfig(
                    temperature=0.2, max_output_tokens=1024,
                    response_mime_type="application/json"
                )
            )
            result = json.loads(response.text.strip())
        except Exception as e:
            print(f"  {i}. {s.get('name','?')}: Gemini verification failed ({e}) — skipped")
            continue
        old = s.get("rating", "🔵")
        new = result.get("rating", old)
        notes = result.get("visual_notes", "").strip()
        s["rating"] = new
        s["verified"] = True
        s["visual_notes"] = notes
        if notes:
            s["notes"] = (s.get("notes", "").rstrip() + f" [SAT✓] {notes}").strip()
        arrow = "=" if old == new else f"{old}→{new}"
        print(f"  {i}. {result.get('verdict','?'):9s} {arrow}  {s.get('name','?')}")
        if notes:
            print(f"     {notes}")
        time.sleep(1)  # gentle on imagery server + model quota
    return spots

# ── Geocode address → lat/lon ─────────────────────────────────────────────────
def geocode(query):
    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1&q="
           + urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": "SpotScout/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        results = json.loads(r.read().decode())
    if not results:
        raise SystemExit(f"Could not geocode: {query}")
    r = results[0]
    return float(r["lat"]), float(r["lon"]), r["display_name"]

# ── Main ──�