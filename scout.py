"""
scout.py — Overnight parking spot finder (agentic tool-calling version)
Gemini acts as an agent: calls get_osm_features to gather map data, then
calls get_satellite_image only for borderline spots before returning results.

Auth: uses application default credentials (run `gcloud auth application-default login` once)
Project: claude-memory-202605
"""

import os, json, time, math, re, argparse, urllib.request, urllib.parse, urllib.error
from datetime import datetime

# ── Gemini setup (google-genai SDK) ──────────────────────────────────────────
try:
    from google import genai
    from google.genai import types
except ImportError:
    raise SystemExit("ERROR: Run `py -m pip install google-genai` first")

GCP_PROJECT  = "claude-memory-202605"
GCP_LOCATION = "us-central1"
MODEL        = "gemini-2.5-flash"

client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)

# ── Overpass / OSM ────────────────────────────────────────────────────────────
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERNIGHT_CHAINS = {
    "walmart":        ("Walmart",             "explicitly allows overnight parking by policy"),
    "wal-mart":       ("Walmart",             "explicitly allows overnight parking by policy"),
    "cracker barrel": ("Cracker Barrel",      "explicitly welcomes RV/car overnight parking"),
    "flying j":       ("Flying J",            "truck stop — overnight parking standard"),
    "pilot":          ("Pilot Travel Center", "truck stop — overnight parking standard"),
    "loves":          ("Love's Travel Stop",  "truck stop — overnight parking standard"),
    "love's":         ("Love's Travel Stop",  "truck stop — overnight parking standard"),
    "cabela":         ("Cabela's",            "large lot, commonly allows overnight stays"),
    "bass pro":       ("Bass Pro Shops",      "large lot, commonly allows overnight stays"),
    "camping world":  ("Camping World",       "RV-focused, overnight usually fine"),
    "sam's club":     ("Sam's Club",          "some locations allow overnight parking"),
    "costco":         ("Costco",              "some locations allow overnight parking"),
    "menards":        ("Menards",             "some locations allow overnight parking"),
}

def build_overpass_query(lat, lon, radius_m=2000):
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
    data  = urllib.parse.urlencode({"data": query}).encode()
    req   = urllib.request.Request(OVERPASS_URL, data=data,
                                   headers={"User-Agent": "SpotScout/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        raise SystemExit(f"Overpass query failed: {e}")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2-lat1)*p/2)**2 +
         math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2)
    return 2*R*math.asin(math.sqrt(a))

def summarise_osm(osm_data, center_lat, center_lon):
    elements = osm_data.get("elements", [])
    roads, parking, landuse, natural_, amenities, tourism, leisure, facilities, lamps = \
        [], [], [], [], [], [], [], [], []
    chain_spots, services = [], []

    for el in elements:
        tags  = el.get("tags", {})
        etype = el.get("type")
        if etype == "node":
            clat, clon = el.get("lat"), el.get("lon")
        elif etype == "way":
            c = el.get("center", {})
            clat, clon = c.get("lat"), c.get("lon")
        else:
            continue
        if not clat or not clon:
            continue

        dist        = haversine(center_lat, center_lon, clat, clon)
        name        = tags.get("name", "")
        hw          = tags.get("highway", "")
        lu          = tags.get("landuse", "")
        nat         = tags.get("natural", "")
        amenity     = tags.get("amenity", "")
        tourism_tag = tags.get("tourism", "")
        leisure_tag = tags.get("leisure", "")
        access      = tags.get("access", "unknown")
        boundary    = tags.get("boundary", "")
        waterway    = tags.get("waterway", "")

        if hw in ("service","track","unclassified","residential","tertiary","road",
                  "rest_area","services"):
            roads.append({"dist_m": int(dist), "type": hw, "name": name,
                          "lat": clat, "lon": clon, "access": access,
                          "tracktype": tags.get("tracktype",""),
                          "surface": tags.get("surface","unknown")})
        elif amenity in ("parking","parking_space","rest_area","truck_stop"):
            parking.append({"dist_m": int(dist), "type": amenity, "name": name,
                            "lat": clat, "lon": clon, "access": access,
                            "fee": tags.get("fee","unknown"),
                            "maxstay": tags.get("maxstay","unknown"),
                            "opening_hours": tags.get("opening_hours","unknown")})
        elif amenity in ("social_facility","library","place_of_worship") or \
             leisure_tag == "fitness_centre":
            stype = "fitness_centre" if leisure_tag == "fitness_centre" else amenity
            services.append({"dist_m": int(dist), "type": stype, "name": name,
                             "lat": clat, "lon": clon,
                             "detail": tags.get("social_facility",""),
                             "opening_hours": tags.get("opening_hours","unknown")})
        elif lu:
            landuse.append({"dist_m": int(dist), "type": lu, "name": name,
                            "lat": clat, "lon": clon})
        elif nat:
            natural_.append({"dist_m": int(dist), "type": nat, "name": name,
                             "lat": clat, "lon": clon})
        elif tourism_tag:
            tourism.append({"dist_m": int(dist), "type": tourism_tag, "name": name,
                            "lat": clat, "lon": clon, "access": access,
                            "fee": tags.get("fee","unknown")})
        elif leisure_tag:
            leisure.append({"dist_m": int(dist), "type": leisure_tag, "name": name,
                            "lat": clat, "lon": clon, "access": access})
        elif amenity in ("fuel","hospital","police","bus_station"):
            amenities.append({"dist_m": int(dist), "type": amenity, "name": name,
                              "lat": clat, "lon": clon})
        elif amenity in ("fast_food","toilets","drinking_water") or \
             tags.get("shop","") in ("convenience","supermarket"):
            ftype = amenity or tags.get("shop","")
            facilities.append({"dist_m": int(dist), "type": ftype, "name": name,
                               "lat": clat, "lon": clon,
                               "opening_hours": tags.get("opening_hours","unknown")})
        elif boundary in ("protected_area","national_park","provincial_park"):
            natural_.append({"dist_m": int(dist), "type": boundary, "name": name,
                             "lat": clat, "lon": clon})
        elif waterway in ("boat_ramp","slipway"):
            leisure.append({"dist_m": int(dist), "type": waterway, "name": name,
                            "lat": clat, "lon": clon, "access": access})
        elif hw == "street_lamp":
            lamps.append({"lat": clat, "lon": clon, "dist_m": int(dist)})

        name_lc = name.lower()
        for key, (chain_name, policy) in OVERNIGHT_CHAINS.items():
            if key in name_lc:
                chain_spots.append({"dist_m": int(dist), "chain": chain_name,
                                    "policy": policy, "lat": clat, "lon": clon})
                break

    roads       = sorted(roads,       key=lambda x: x["dist_m"])[:35]
    parking     = sorted(parking,     key=lambda x: x["dist_m"])[:20]
    landuse     = sorted(landuse,     key=lambda x: x["dist_m"])[:20]
    natural_    = sorted(natural_,    key=lambda x: x["dist_m"])[:15]
    tourism     = sorted(tourism,     key=lambda x: x["dist_m"])[:15]
    leisure     = sorted(leisure,     key=lambda x: x["dist_m"])[:15]
    amenities   = sorted(amenities,   key=lambda x: x["dist_m"])[:10]
    facilities  = sorted(facilities,  key=lambda x: x["dist_m"])[:15]
    lamps       = sorted(lamps,       key=lambda x: x["dist_m"])[:100]
    chain_spots = sorted(chain_spots, key=lambda x: x["dist_m"])
    services    = sorted(services,    key=lambda x: x["dist_m"])[:15]

    return {
        "center":            {"lat": center_lat, "lon": center_lon},
        "roads":             roads,
        "parking_nodes":     parking,
        "landuse_zones":     landuse,
        "natural_cover":     natural_,
        "tourism_sites":     tourism,
        "leisure_areas":     leisure,
        "nearby_amenities":  amenities,
        "facilities_24h":    facilities,
        "street_lamps":      lamps,
        "overnight_chains":  chain_spots,
        "support_services":  services,
        "total_elements":    len(elements),
    }

# ── Satellite imagery ─────────────────────────────────────────────────────────
SAT_EXPORT_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services"
                  "/World_Imagery/MapServer/export")

def fetch_sat_image(lat, lon, span_m=250, size=512):
    dlat  = span_m / 2 / 111320
    dlon  = span_m / 2 / (111320 * math.cos(math.radians(lat)))
    params = urllib.parse.urlencode({
        "bbox":    f"{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}",
        "bboxSR":  "4326", "imageSR": "3857",
        "size":    f"{size},{size}", "format": "jpg", "f": "image",
    })
    req = urllib.request.Request(f"{SAT_EXPORT_URL}?{params}",
                                 headers={"User-Agent": "SpotScout/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def _tool_satellite(lat, lon, span_m=250, context=""):
    """Fetch satellite image and return a structured visual assessment dict."""
    try:
        img = fetch_sat_image(lat, lon, span_m=span_m)
    except Exception as e:
        return {"error": f"Image fetch failed: {e}"}

    prompt = (
        f"Visually assess this satellite image for overnight vehicle parking suitability.\n"
        f"Image covers ~{span_m}m centered on ({lat:.5f}, {lon:.5f}).\n"
        + (f"OSM context: {context}\n" if context else "")
        + "\nAssess:\n"
        "- COVER: tree canopy, buildings, or terrain shielding a parked vehicle\n"
        "- SIGHTLINES: visible from main roads or residential windows?\n"
        "- SURFACE: paved, gravel, grass, or undrivable?\n"
        "- ACCESS: open, gated, or unclear?\n"
        "- CONTEXT: residential, commercial, industrial, or isolated?\n\n"
        'Return ONLY valid JSON:\n'
        '{"cover":"good|partial|none","sightlines":"exposed|partial|concealed",'
        '"surface":"paved|gravel|grass|undrivable","access":"open|gated|unclear",'
        '"context":"residential|commercial|industrial|isolated|mixed",'
        '"assessment":"1-2 sentence overnight parking suitability summary"}'
    )
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=[types.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(
                temperature=0.1, max_output_tokens=512,
                response_mime_type="application/json",
            )
        )
        return json.loads(resp.text.strip())
    except Exception as e:
        return {"error": f"Vision analysis failed: {e}"}

# ── Geocode ───────────────────────────────────────────────────────────────────
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

# ── Prompt building ───────────────────────────────────────────────────────────
MODE_PREFIXES = {
    "stealth":     "PRIORITY MODE — STEALTH: rank concealment above all. Prefer deep cover, unpaved tracks, forest edges, industrial dead-ends. Deprioritize facilities distance.",
    "convenience": "PRIORITY MODE — CONVENIENCE: rank proximity to 24h facilities (fuel, fast food, toilets, water) above stealth. Accept visible spots if bathroom/water is walking distance.",
    "safe":        "PRIORITY MODE — SAFETY: rank ambient light and nearby 24h human activity above stealth. Avoid complete isolation. Some visibility is acceptable for security.",
    "default":     "",
}

SYSTEM_PROMPT = """You are an expert at finding discreet overnight vehicle parking spots for someone living in their car.

You have three tools:
- get_osm_features: fetch OpenStreetMap data around a coordinate (always call this first)
- get_satellite_image: fetch a satellite photo and get a visual cover/surface/access assessment
  (use only for borderline spots — skip confirmed overnight_chains or labeled truck stops)
- geocode_location: convert a place name to coordinates (only if you receive a name instead of coords)

Workflow:
1. Call get_osm_features to get map data for the area.
2. Identify up to 6 candidate spots from the OSM data.
3. For any candidate where cover, surface, or access is genuinely ambiguous from OSM alone,
   call get_satellite_image to verify before finalising its rating.
4. Return your final answer as ONLY a valid JSON array — no markdown, no prose outside the JSON.

Scoring criteria for each spot:
- STEALTH: low visibility from main roads, residential areas, or patrol routes
- SAFETY: not dangerously isolated; some ambient light or activity nearby
- PRACTICALITY: flat, no posted time limits, legal or low-enforcement risk
- COVER: trees, buildings, or terrain shielding the vehicle
- FACILITIES: distance to 24h fuel, fast food, toilets, convenience stores
- LIGHTING: high lamp density within 50m = high exposure; prefer lamps 100m+ away
- CHAINS: overnight_chains entries are gold-standard; always list any within range first
- SERVICES: libraries (Wi-Fi/warmth/charging), gyms (showers), social facilities (meals)

Good spot types: industrial/commercial lots at night, forest service roads, church parking,
dead-end service roads, large retail back lots, rest areas, truck stops, campgrounds.
Bad spot types: residential streets, high-foot-traffic areas, near police stations.

Each spot in the final JSON array must have exactly these fields:
{
  "id": "spot_<unix_timestamp>_<index>",
  "name": "short descriptive name",
  "lat": <float>,
  "lon": <float>,
  "rating": "🟢" | "🟡" | "🔴",
  "notes": "2-3 sentence assessment: why this spot, risks, best time to arrive",
  "created": "<ISO datetime string>"
}"""

def _build_feedback_block(feedback):
    if not feedback:
        return ""
    knocked = [s for s in feedback if s.get("fieldStatus") == "knocked"]
    visited = [s for s in feedback if s.get("fieldStatus") == "visited"]
    lines   = []
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

# ── JSON spot parser (shared) ─────────────────────────────────────────────────
def _parse_spots(text):
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:]).rstrip("`").strip()

    start = text.find("[")
    if start != -1:
        text = text[start:]

    try:
        spots = json.loads(text)
    except json.JSONDecodeError:
        last_close = text.rfind("}")
        if last_close != -1:
            spots = json.loads(text[:last_close+1] + "\n]")
        else:
            raise SystemExit(f"Failed to parse agent response as JSON.\nRaw: {text[:500]}")

    if not isinstance(spots, list):
        spots = [spots]

    ts = int(time.time())
    for i, s in enumerate(spots):
        s["id"] = f"spot_{ts}_{i}"
        s.setdefault("created", datetime.utcnow().isoformat() + "Z")
        s.setdefault("rating",  "🔵")
        s.setdefault("notes",   "")
        s.setdefault("name",    f"Spot {i+1}")
        if "lon" in s and "lng" not in s:
            s["lng"] = s.pop("lon")
    return spots

# ── Agentic tool-calling loop ─────────────────────────────────────────────────
_FN_OSM = types.FunctionDeclaration(
    name="get_osm_features",
    description=(
        "Fetch and summarize OpenStreetMap features within a radius of the given coordinates. "
        "Returns roads, parking areas, landuse zones, natural cover, amenities, tourism sites, "
        "24h facilities, street lamps, and overnight-friendly chain store locations."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "lat":      types.Schema(type=types.Type.NUMBER,  description="Latitude of center point"),
            "lon":      types.Schema(type=types.Type.NUMBER,  description="Longitude of center point"),
            "radius_m": types.Schema(type=types.Type.INTEGER, description="Search radius in metres (default 2000, max 5000)"),
        },
        required=["lat", "lon"],
    )
)

_FN_SAT = types.FunctionDeclaration(
    name="get_satellite_image",
    description=(
        "Fetch a satellite image for a specific coordinate and return a visual assessment of "
        "cover, sightlines, surface, access, and context. Use for ambiguous spots only — "
        "skip confirmed overnight chains or clearly labelled truck stops."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "lat":     types.Schema(type=types.Type.NUMBER,  description="Latitude of the spot"),
            "lon":     types.Schema(type=types.Type.NUMBER,  description="Longitude of the spot"),
            "span_m":  types.Schema(type=types.Type.INTEGER, description="Image width in metres (default 250)"),
            "context": types.Schema(type=types.Type.STRING,  description="Brief OSM-derived description of this spot"),
        },
        required=["lat", "lon"],
    )
)

_FN_GEO = types.FunctionDeclaration(
    name="geocode_location",
    description="Convert a place name or address to latitude/longitude coordinates.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(type=types.Type.STRING, description="Place name or address to geocode"),
        },
        required=["query"],
    )
)

_TOOLS = types.Tool(function_declarations=[_FN_OSM, _FN_SAT, _FN_GEO])


def run_agent(lat, lon, radius_m=2000, mode="default", feedback=None):
    mode_line      = MODE_PREFIXES.get(mode, "")
    feedback_block = _build_feedback_block(feedback)

    initial_msg = (
        f"Find the best overnight parking spots near ({lat}, {lon}).\n"
        + (f"{mode_line}\n" if mode_line else "")
        + (f"{feedback_block}\n" if feedback_block else "")
        + f"Default search radius: {radius_m}m (adjust via get_osm_features if needed).\n"
        "Start with get_osm_features. Call get_satellite_image for any spot where "
        "cover or access is ambiguous from OSM data alone.\n"
        "When finished, return ONLY a valid JSON array of up to 6 spots — no markdown, "
        "no prose — using the schema from your instructions."
    )

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=initial_msg)])]
    config   = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[_TOOLS],
        temperature=0.3,
        max_output_tokens=8192,
    )

    final_text = ""
    for iteration in range(12):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=contents, config=config
            )
        except Exception as e:
            raise SystemExit(f"Gemini request failed: {e}")

        candidate = response.candidates[0]
        contents.append(candidate.content)

        fn_calls = [p for p in candidate.content.parts
                    if hasattr(p, "function_call") and p.function_call]

        if not fn_calls:
            text_parts = [p.text for p in candidate.content.parts
                          if hasattr(p, "text") and p.text]
            final_text = " ".join(text_parts).strip()
            break

        # Execute every tool call the model requested
        fn_responses = []
        for part in fn_calls:
            fc   = part.function_call
            args = dict(fc.args)
            label = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            print(f"  → {fc.name}({label})")

            try:
                if fc.name == "get_osm_features":
                    osm_data = query_overpass(
                        float(args["lat"]), float(args["lon"]),
                        int(args.get("radius_m", radius_m)),
                    )
                    result = summarise_osm(osm_data, float(args["lat"]), float(args["lon"]))
                elif fc.name == "get_satellite_image":
                    result = _tool_satellite(
                        float(args["lat"]), float(args["lon"]),
                        int(args.get("span_m", 250)),
                        str(args.get("context", "")),
                    )
                elif fc.name == "geocode_location":
                    g_lat, g_lon, g_name = geocode(str(args["query"]))
                    result = {"lat": g_lat, "lon": g_lon, "display_name": g_name}
                else:
                    result = {"error": f"Unknown tool: {fc.name}"}
            except Exception as e:
                result = {"error": str(e)}

            fn_responses.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )

        contents.append(types.Content(role="user", parts=fn_responses))
    else:
        raise SystemExit("Agent exceeded iteration limit without returning spots.")

    return _parse_spots(final_text)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Scout overnight parking spots with an AI agent")
    ap.add_argument("location",
                    help='Address, city name, or "lat,lon" coordinates')
    ap.add_argument("--radius",   type=int, default=2000,
                    help="Default search radius in metres (default 2000)")
    ap.add_argument("--mode",     choices=["stealth","convenience","safe","default"],
                    default="default",
                    help="Scoring priority mode")
    ap.add_argument("--append",   action="store_true",
                    help="Merge new spots into existing output file instead of overwriting")
    ap.add_argument("--feedback", metavar="FILE",
                    help="JSON file with field-tested spot feedback (fieldStatus: visited/knocked)")
    ap.add_argument("--output",   metavar="FILE",
                    help="Output JSON file (default: spots_<location>.json)")
    args = ap.parse_args()

    # Resolve coordinates
    loc = args.location.strip()
    parts = loc.replace(" ","").split(",")
    try:
        if len(parts) == 2:
            lat, lon  = float(parts[0]), float(parts[1])
            display   = loc
        else:
            raise ValueError
    except ValueError:
        print(f"Geocoding: {loc}")
        lat, lon, display = geocode(loc)
        print(f"  → {display} ({lat:.5f}, {lon:.5f})")

    # Load feedback
    feedback = None
    if args.feedback:
        try:
            with open(args.feedback) as f:
                feedback = json.load(f)
            print(f"Loaded {len(feedback)} feedback spots from {args.feedback}")
        except Exception as e:
            print(f"Warning: could not load feedback file: {e}")

    # Resolve output path
    if args.output:
        out_file = args.output
    else:
        safe     = re.sub(r"[^\w\-]", "_", display.split(",")[0])[:30]
        out_file = f"spots_{safe}.json"

    print(f"\nScout: {display} | radius {args.radius}m | mode {args.mode}")
    print("Running agent...\n")

    spots = run_agent(lat, lon, args.radius, args.mode, feedback)

    # Append mode — merge with existing file, deduplicate by id
    if args.append and os.path.exists(out_file):
        try:
            with open(out_file) as f:
                existing      = json.load(f)
            existing_ids  = {s.get("id") for s in existing}
            new_spots     = [s for s in spots if s.get("id") not in existing_ids]
            spots         = existing + new_spots
            print(f"Merged {len(new_spots)} new spots with {len(existing)} existing → {len(spots)} total")
        except Exception as e:
            print(f"Warning: could not load existing file for append: {e}")

    with open(out_file, "w") as f:
        json.dump(spots, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(spots)} spots → {out_file}")
    for s in spots:
        r            = s.get("rating", "🔵")
        name         = s.get("name", "?")
        notes_short  = s.get("notes", "")[:80]
        verified_tag = " [SAT✓]" if s.get("verified") else ""
        print(f"  {r}{verified_tag} {name}: {notes_short}")


if __name__ == "__main__":
    main()
