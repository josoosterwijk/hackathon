
import os
import re
import json
import math
import time
import datetime
from typing import Optional, Tuple, Dict, Any

import requests
import streamlit as st

st.set_page_config(page_title="Cartographer's Curse — Address → Decision", page_icon="🧭", layout="centered")
st.title("🧭 Cartographer's Curse (MVP) — Address → Simple/Complex")
st.caption("Voer enkel een adres in. De app zoekt context (OSM/Overpass), maakt een Street View-link, en past de 3 regels toe.")

# --------------------------- Config ------------------------------------------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")  # optional
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# --------------------------- Utils -------------------------------------------
def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    """
    Try Google Geocoding (if key set), else Nominatim.
    Returns (lat, lon) or None.
    """
    if GOOGLE_API_KEY:
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address, "key": GOOGLE_API_KEY},
                timeout=15
            )
            if resp.ok:
                data = resp.json()
                if data.get("results"):
                    loc = data["results"][0]["geometry"]["location"]
                    return loc["lat"], loc["lng"]
        except Exception:
            pass

    # Fallback to Nominatim (polite usage headers)
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "jsonv2", "addressdetails": 1, "limit": 1},
            headers={"User-Agent": "telenet-hackathon-agent/1.0"},
            timeout=20
        )
        if resp.ok:
            arr = resp.json()
            if arr:
                return float(arr[0]["lat"]), float(arr[0]["lon"])
    except Exception:
        pass
    return None

def street_view_link(lat: float, lon: float) -> str:
    """
    Construct a Google Maps Street View link centered at the coordinate.
    We do not fetch imagery.
    """
    return f"https://www.google.com/maps?q&layer=c&cbll={lat},{lon}&cbp=11,0,0,0,0"

def overpass_query(query: str) -> Dict[str, Any]:
    r = requests.post(OVERPASS_URL, data={"data": query}, timeout=60,
                      headers={"User-Agent": "telenet-hackathon-agent/1.0"})
    r.raise_for_status()
    return r.json()

def overpass_bbox(lat: float, lon: float, meters: float = 60) -> str:
    """
    Create a small bbox around the point (approx using 1 deg ~ 111,111 m).
    """
    dlat = meters / 111111.0
    dlon = meters / (111111.0 * math.cos(math.radians(lat)))
    south = lat - dlat
    north = lat + dlat
    west = lon - dlon
    east = lon + dlon
    return f"{south},{west},{north},{east}"

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# --------------------------- Feature Retrieval --------------------------------
def fetch_context(lat: float, lon: float) -> Dict[str, Any]:
    """
    Pull nearby OSM features: building, sidewalks, poles.
    """
    bbox = overpass_bbox(lat, lon, meters=80)
    q = f"""
    [out:json][timeout:25];
    (
      way["building"]({bbox});
      relation["building"]({bbox});

      way["highway"]["sidewalk"]({bbox});
      way["highway"="footway"]["footway"="sidewalk"]({bbox});

      node["highway"="street_lamp"]({bbox});  // not a pole, but a proxy for streetscape
      node["power"="pole"]({bbox});
      node["man_made"="utility_pole"]({bbox});
      node["telecom"="pole"]({bbox});

      node["man_made"="street_cabinet"]({bbox});
      node["telecom"="cabinet"]({bbox});
    );
    out body center qt;
    """
    try:
        data = overpass_query(q)
        return data
    except Exception as e:
        return {"error": str(e)}

def presence_sidewalk(elements, lat, lon, radius_m=25) -> bool:
    for el in elements:
        if el.get("type") == "way":
            tags = el.get("tags", {})
            if "sidewalk" in tags or (tags.get("highway") == "footway" and tags.get("footway") == "sidewalk"):
                if "center" in el:
                    d = haversine(lat, lon, el["center"]["lat"], el["center"]["lon"])
                    if d <= radius_m:
                        return True
    return False

def nearest_pole(elements, lat, lon):
    cand = []
    for el in elements:
        if el.get("type") == "node":
            tags = el.get("tags", {})
            if tags.get("power") == "pole" or tags.get("man_made") == "utility_pole" or tags.get("telecom") == "pole":
                d = haversine(lat, lon, el["lat"], el["lon"])
                cand.append((d, el["lat"], el["lon"]))
    cand.sort(key=lambda x: x[0])
    if cand:
        d, plat, plon = cand[0]
        return d, plat, plon
    return None

def nearest_cabinet(elements, lat, lon):
    cand = []
    for el in elements:
        if el.get("type") == "node":
            tags = el.get("tags", {})
            if tags.get("man_made") == "street_cabinet" or tags.get("telecom") == "cabinet":
                d = haversine(lat, lon, el["lat"], el["lon"])
                cand.append((d, el["lat"], el["lon"]))
    cand.sort(key=lambda x: x[0])
    if cand:
        d, clat, clon = cand[0]
        return d, clat, clon
    return None

# --------------------------- Rule Engine --------------------------------------
def classify_auto(lat: float, lon: float, ctx: Dict[str, Any]) -> Dict[str, Any]:
    elements = ctx.get("elements", [])
    has_sidewalk = presence_sidewalk(elements, lat, lon, radius_m=25)
    pole_info = nearest_pole(elements, lat, lon)
    cab_info = nearest_cabinet(elements, lat, lon)

    # Infer network type
    if pole_info:
        inferred_type = "aerial"
    elif has_sidewalk:
        inferred_type = "underground"
    else:
        inferred_type = "facade"

    # Estimate metrics
    facade_len_m = None
    aerial_height_m = None
    public_dig_required = None

    if inferred_type == "facade":
        if cab_info and cab_info[0] < 120:
            facade_len_m = round(cab_info[0], 1)
        else:
            facade_len_m = 35.0  # default estimate with uncertainty

    if inferred_type == "aerial":
        if pole_info:
            aerial_height_m = 8.5 if pole_info[0] < 20 else 9.0
        else:
            aerial_height_m = 8.5

    if inferred_type == "underground":
        public_dig_required = bool(has_sidewalk)

    # Apply rules
    reasons = []
    votes = []

    if inferred_type == "facade":
        if facade_len_m is None:
            votes.append("borderline")
            reasons.append("Façade: lengte onbekend → borderline.")
        else:
            if facade_len_m > 50:
                votes.append("complex")
                reasons.append(f"Façade lengte {facade_len_m:.1f} m > 50 m → complex.")
            else:
                votes.append("simple")
                reasons.append(f"Façade lengte {facade_len_m:.1f} m ≤ 50 m → simple.")

    if inferred_type == "aerial":
        if aerial_height_m is None:
            votes.append("borderline")
            reasons.append("Aerial: hoogte onbekend → borderline.")
        else:
            if aerial_height_m > 8:
                votes.append("complex")
                reasons.append(f"Aerial bevestiging {aerial_height_m:.1f} m > 8 m → complex.")
            else:
                votes.append("simple")
                reasons.append(f"Aerial bevestiging {aerial_height_m:.1f} m ≤ 8 m → simple.")

    if inferred_type == "underground":
        if public_dig_required is None:
            votes.append("borderline")
            reasons.append("Underground: public digging onbekend → borderline.")
        else:
            if public_dig_required:
                votes.append("complex")
                reasons.append("Underground: publieke zone (stoep) kruist → complex.")
            else:
                votes.append("simple")
                reasons.append("Underground: enkel private zone → simple.")

    if "complex" in votes:
        label = "complex"
    elif "borderline" in votes and "simple" not in votes:
        label = "borderline"
    else:
        label = "simple"

    # Risk score
    risk = 0.35
    if inferred_type == "facade" and (cab_info is None):
        risk += 0.25
    if inferred_type == "aerial" and (pole_info is None):
        risk += 0.25
    if inferred_type == "underground" and (public_dig_required is None):
        risk += 0.25
    if label == "borderline":
        risk = max(risk, 0.55)
    risk = round(min(1.0, max(0.0, risk)), 2)

    return {
        "label": label,
        "network_type": inferred_type,
        "facade_length_m": facade_len_m,
        "aerial_height_m": aerial_height_m,
        "public_dig_required": public_dig_required,
        "has_sidewalk_nearby": has_sidewalk,
        "nearest_pole_m": round(pole_info[0],1) if pole_info else None,
        "nearest_cabinet_m": round(cab_info[0],1) if cab_info else None,
        "risk_score": risk,
        "reasons": reasons
    }

# --------------------------- UI ----------------------------------------------
with st.form("addr_form"):
    address = st.text_input("Adres (Wallonië)", placeholder="bv. Rue du Pont 12, 4000 Liège, België")
    submitted = st.form_submit_button("Analyseer")

if submitted and address.strip():
    with st.spinner("Zoeken naar locatie en OSM-context..."):
        coords = geocode_address(address.strip())

    if not coords:
        st.error("Kon het adres niet geocoderen. Probeer een volledig adres of check je internetverbinding.")
        st.stop()

    lat, lon = coords
    sv_url = street_view_link(lat, lon)

    st.markdown("### 📍 Gevonden locatie")
    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**Lat**: {lat:.6f}")
        st.write(f"**Lon**: {lon:.6f}")
    with col2:
        st.link_button("Open Street View", sv_url)

    ctx = fetch_context(lat, lon)

    if "error" in ctx:
        st.warning("Kon OSM-context niet ophalen (Overpass). Resultaat kan onnauwkeurig zijn.")
        analysis = {
            "label": "borderline",
            "network_type": "facade",
            "facade_length_m": None,
            "aerial_height_m": None,
            "public_dig_required": None,
            "has_sidewalk_nearby": None,
            "nearest_pole_m": None,
            "nearest_cabinet_m": None,
            "risk_score": 0.7,
            "reasons": ["Geen contextdata → markeer als borderline en stuur naar review."]
        }
    else:
        analysis = classify_auto(lat, lon, ctx)

    st.divider()
    st.markdown("### 🧮 Resultaat")
    st.metric("Beslissing", analysis["label"].upper())
    st.metric("Geschat netwerktype", analysis["network_type"])
    st.metric("Risicoscore (0–1)", analysis["risk_score"])

    st.markdown("**Redenen**")
    for r in analysis["reasons"]:
        st.write("- " + r)

    st.markdown("**Velden**")
    st.json({
        "address": address.strip(),
        "coords": {"lat": lat, "lon": lon},
        "network_type": analysis["network_type"],
        "facade_length_m": analysis["facade_length_m"],
        "aerial_height_m": analysis["aerial_height_m"],
        "public_dig_required": analysis["public_dig_required"],
        "has_sidewalk_nearby": analysis["has_sidewalk_nearby"],
        "nearest_pole_m": analysis["nearest_pole_m"],
        "nearest_cabinet_m": analysis["nearest_cabinet_m"],
        "street_view_url": sv_url
    })

    # Auto-flag if risky or borderline
    flagged = analysis["risk_score"] >= 0.6 or analysis["label"] == "borderline"
    if flagged:
        st.warning("⚠️ Case automatisch geflagd voor menselijke review (hoge risico of borderline).")
    else:
        st.success("✅ Lage risico — geen review vereist.")

    # Save option
    st.divider()
    st.subheader("Record opslaan (optioneel)")
    case_id = st.text_input("Case ID / label", value=address.strip())
    if st.button("Append to classifications.jsonl"):
        record = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "case_id": case_id or None,
            "address": address.strip(),
            "coords": {"lat": lat, "lon": lon},
            "decision": analysis["label"],
            "risk_score": analysis["risk_score"],
            "reasons": analysis["reasons"],
            "fields": {
                "network_type": analysis["network_type"],
                "facade_length_m": analysis["facade_length_m"],
                "aerial_height_m": analysis["aerial_height_m"],
                "public_dig_required": analysis["public_dig_required"],
                "has_sidewalk_nearby": analysis["has_sidewalk_nearby"],
                "nearest_pole_m": analysis["nearest_pole_m"],
                "nearest_cabinet_m": analysis["nearest_cabinet_m"],
            },
            "links": {"street_view": sv_url}
        }
        with open("classifications.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        st.success("Opgeslagen naar classifications.jsonl")
else:
    st.info("Geef een adres in en klik op **Analyseer**. Tip: Zet GOOGLE_API_KEY als environment variable voor Google Geocoding; anders wordt Nominatim gebruikt.")
