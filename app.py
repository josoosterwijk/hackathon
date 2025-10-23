
import re
import json
import math
import datetime
import streamlit as st

st.set_page_config(page_title="Install Complexity Checker", page_icon="🧭", layout="centered")

st.title("🧭 Install Complexity Checker — Wallonia (MVP)")
st.caption("Paste a Google Street View link (or any map link), fill the quick facts, and get a simple/complex decision. No imagery is downloaded or stored.")

with st.expander("How this works (and ToS-friendly)"):
    st.markdown(
        "- We do **not** download or store Google Street View images.\n"
        "- You paste a link; we only parse coordinates/pano id **if present** to include in the record.\n"
        "- You provide quick measurements/observations, and we apply the three rules:\n"
        "  1) **Façade network**: simple unless length > **50 m**\n"
        "  2) **Aerial network**: simple unless height > **8 m**\n"
        "  3) **Underground**: simple unless **digging in public area** is required"
    )

# --- Helpers -----------------------------------------------------------------

def extract_coords_and_pano(url: str):
    """Best-effort extraction of lat/lon and pano id from a Google Maps/Street View link."""
    lat = lon = pano = None

    # Pattern 1: @lat,lon,zoomz
    m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+),', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))

    # Pattern 2: !3dLAT!4dLON
    m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))

    # Pattern 3: pano=XXXXXXXXXXXX (Static API style)
    m = re.search(r'[?&]pano=([\w-]+)', url)
    if m:
        pano = m.group(1)

    # Pattern 4: google street view share URLs sometimes include 'cbp' or 'cbll'
    m = re.search(r'[?&]cbll=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))

    return lat, lon, pano

def classify(facade_len_m, aerial_height_m, public_dig_required, provided_network_type):
    """Apply the three rules and return label + reasons."""
    reasons = []
    votes = []  # each rule casts 'simple' or 'complex'

    # Determine implied network type if user leaves it 'auto'
    net_type = provided_network_type
    if net_type == "auto":
        # Very naive heuristic: if aerial height provided -> aerial takes precedence;
        # else if facade length provided -> facade; else underground.
        if aerial_height_m is not None:
            net_type = "aerial"
        elif facade_len_m is not None:
            net_type = "facade"
        else:
            net_type = "underground"

    # Rule 1: Facade
    if net_type == "facade":
        if facade_len_m is None:
            reasons.append("Façade: length unknown → cannot decide; marking as borderline.")
            votes.append("borderline")
        else:
            if facade_len_m > 50:
                votes.append("complex")
                reasons.append(f"Façade length {facade_len_m:.1f} m > 50 m → complex.")
            else:
                votes.append("simple")
                reasons.append(f"Façade length {facade_len_m:.1f} m ≤ 50 m → simple.")

    # Rule 2: Aerial
    if net_type == "aerial":
        if aerial_height_m is None:
            reasons.append("Aerial: attachment height unknown → cannot decide; marking as borderline.")
            votes.append("borderline")
        else:
            if aerial_height_m > 8:
                votes.append("complex")
                reasons.append(f"Aerial attachment {aerial_height_m:.1f} m > 8 m → complex.")
            else:
                votes.append("simple")
                reasons.append(f"Aerial attachment {aerial_height_m:.1f} m ≤ 8 m → simple.")

    # Rule 3: Underground
    if net_type == "underground":
        if public_dig_required is None:
            reasons.append("Underground: public digging unknown → cannot decide; marking as borderline.")
            votes.append("borderline")
        else:
            if public_dig_required:
                votes.append("complex")
                reasons.append("Underground path requires digging in public area → complex.")
            else:
                votes.append("simple")
                reasons.append("Underground path stays on private domain only → simple.")

    # Aggregate
    if "complex" in votes:
        label = "complex"
    elif "borderline" in votes and "simple" not in votes:
        label = "borderline"
    else:
        label = "simple"

    return label, reasons, net_type

def risk_score(facade_len_m, aerial_height_m, public_dig_required, label):
    """Simple confidence heuristic based on distance to thresholds and missing info."""
    # Start with high confidence
    confidence = 1.0
    missing = 0

    # Distance-to-threshold dampening
    if facade_len_m is not None:
        # 50m threshold; within +/-5m → reduce confidence
        dist = abs(facade_len_m - 50)
        if dist < 5:
            confidence *= 0.6
        elif dist < 10:
            confidence *= 0.8
    else:
        missing += 1

    if aerial_height_m is not None:
        # 8m threshold; within +/-0.8m → reduce confidence
        dist = abs(aerial_height_m - 8)
        if dist < 0.8:
            confidence *= 0.6
        elif dist < 1.6:
            confidence *= 0.8
    else:
        missing += 1

    if public_dig_required is None:
        missing += 1

    # Penalize missing fields
    if missing == 1:
        confidence *= 0.85
    elif missing == 2:
        confidence *= 0.65
    elif missing >= 3:
        confidence *= 0.5

    # If label is 'borderline', clip confidence further
    if label == "borderline":
        confidence = min(confidence, 0.5)

    # Convert to risk (higher = riskier)
    risk = 1.0 - confidence
    return round(risk, 2), round(confidence, 2)

# --- Form --------------------------------------------------------------------

st.subheader("1) Paste the link")
url = st.text_input("Google Street View (or Google Maps) URL", placeholder="https://www.google.com/maps/@...")

lat = lon = pano = None
if url:
    lat, lon, pano = extract_coords_and_pano(url)

col1, col2, col3 = st.columns(3)
with col1:
    st.write(f"**Lat**: {lat if lat is not None else '—'}")
with col2:
    st.write(f"**Lon**: {lon if lon is not None else '—'}")
with col3:
    st.write(f"**Pano ID**: {pano if pano is not None else '—'}")

st.markdown("Open the link in a new tab to visually inspect it:")
if url:
    st.link_button("Open Street View / Map link", url)

st.divider()
st.subheader("2) Fill the quick facts (your observations)")

net_type = st.selectbox("Network type (choose if known, else 'auto')", options=["auto","facade","aerial","underground"], index=0)

c1, c2 = st.columns(2)
with c1:
    facade_len = st.number_input("Façade path length (m)", min_value=0.0, step=1.0, help="Distance from TAP to entry along façade; >50 m → complex", value=0.0, format="%.1f")
    facade_known = st.checkbox("Façade length known", value=False)
with c2:
    aerial_height = st.number_input("Aerial attachment height (m)", min_value=0.0, step=0.1, help="Attachment on pole/building; >8 m → complex", value=0.0, format="%.1f")
    aerial_known = st.checkbox("Aerial height known", value=False)

public_dig_state = st.selectbox("Underground: is digging in public area required?", options=["unknown","no","yes"], index=0)

facade_len_m = facade_len if facade_known else None
aerial_height_m = aerial_height if aerial_known else None
public_dig_required = None if public_dig_state == "unknown" else (public_dig_state == "yes")

st.divider()
st.subheader("3) Decide")

label, reasons, resolved_net_type = classify(facade_len_m, aerial_height_m, public_dig_required, net_type)
risk, confidence = risk_score(facade_len_m, aerial_height_m, public_dig_required, label)

st.metric("Decision", label.upper())
st.metric("Risk score (0–1, higher = riskier)", risk)
st.progress(1.0 - risk)

st.markdown("**Reasons**")
for r in reasons:
    st.write("- " + r)

st.markdown("**Fields**")
st.json({
    "network_type": resolved_net_type,
    "facade_length_m": facade_len_m,
    "aerial_height_m": aerial_height_m,
    "public_dig_required": public_dig_required,
    "coords": {"lat": lat, "lon": lon, "pano": pano}
})

# --- Save a record -----------------------------------------------------------

st.divider()
st.subheader("4) Save record (optional)")
case_id = st.text_input("Case ID / Address label", placeholder="e.g., 12 Rue Exemple, Liège")
if st.button("Append to classifications.jsonl"):
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "case_id": case_id or None,
        "url": url or None,
        "coords": {"lat": lat, "lon": lon, "pano": pano},
        "decision": label,
        "risk_score": risk,
        "confidence": confidence,
        "reasons": reasons,
        "fields": {
            "network_type": resolved_net_type,
            "facade_length_m": facade_len_m,
            "aerial_height_m": aerial_height_m,
            "public_dig_required": public_dig_required
        }
    }
    with open("classifications.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    st.success("Appended to classifications.jsonl (in the current working directory).")

st.caption("MVP limitations: measurements are user-provided. No imagery is downloaded. For automation later, plug in open street-level imagery (Mapillary) and GIS layers.")
