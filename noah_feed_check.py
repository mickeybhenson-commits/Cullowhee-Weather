#!/usr/bin/env python3
"""
noah_feed_check.py — why are the live inputs empty?

Answers one question, in text, without Streamlit: for every stage collection the
panel reads, did we FAIL TO READ IT, or did we read it and find NOTHING THERE?
Those are opposite states and the panel used to render them identically.

    python noah_feed_check.py

Stdlib + whatever the panel already needs. Read-only; writes nothing.
"""
import sys
import traceback

print("=" * 72)
print("NOAH feed check")
print("=" * 72)

# --- 1. can we even import the SDK? ---------------------------------------
try:
    from google.cloud import firestore
    print("firestore SDK      OK")
except Exception as e:
    print(f"firestore SDK      FAIL — {type(e).__name__}: {e}")
    print("\n  fix: pip install google-cloud-firestore")
    sys.exit(1)

# --- 2. what does the panel think it is connecting to? --------------------
PROJECT_ID = DATABASE = None
try:
    src = open("streamlit_app.py", encoding="utf-8").read()
    for line in src.splitlines():
        t = line.strip()
        if t.startswith("PROJECT_ID"):
            PROJECT_ID = t.split("=", 1)[1].strip().strip('"\'')
        elif t.startswith("DATABASE"):
            DATABASE = t.split("=", 1)[1].strip().strip('"\'')
except Exception as e:
    print(f"could not read streamlit_app.py for config: {e}")

print(f"PROJECT_ID         {PROJECT_ID!r}")
print(f"DATABASE           {DATABASE!r}")

# --- 3. credentials -------------------------------------------------------
import os
adc = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
print(f"GOOGLE_APPLICATION_CREDENTIALS  {adc or '(unset)'}")
secrets = os.path.exists(".streamlit/secrets.toml")
print(f".streamlit/secrets.toml         {'present' if secrets else 'ABSENT'}")
if not adc and not secrets:
    print("  -> no explicit credentials; the client will try Application Default")
    print("     Credentials (gcloud auth application-default login), and will fail")
    print("     with DefaultCredentialsError if none are configured.")

# --- 4. connect -----------------------------------------------------------
print("-" * 72)
try:
    db = firestore.Client(project=PROJECT_ID, database=DATABASE)
    print("client             created")
except Exception as e:
    print(f"client             FAIL — {type(e).__name__}: {e}")
    print("\nThis is the answer: the panel cannot READ the creek. That is not the")
    print("same as the creek being quiet, and must never be displayed as NORMAL.")
    sys.exit(2)

# --- 5. read every collection the panel reads -----------------------------
try:
    import flood_network
    colls = [(sid, s.get("stage_coll")) for sid, s in flood_network.SITES.items()]
except Exception as e:
    print(f"flood_network unavailable ({e}); falling back to a bare listing")
    colls = []

print("-" * 72)
print(f"{'site':<18}{'collection':<28}{'state':<10}detail")
print("-" * 72)

worst = "ok"
for sid, coll in colls:
    if not coll:
        print(f"{sid:<18}{'—':<28}{'unconfig':<10}no stage_coll for this site")
        continue
    try:
        docs = list(db.collection(coll).limit(2000).stream())
    except Exception as e:
        print(f"{sid:<18}{coll:<28}{'ERROR':<10}{type(e).__name__}: {str(e)[:60]}")
        worst = "error"
        continue
    if not docs:
        print(f"{sid:<18}{coll:<28}{'empty':<10}collection read, 0 documents")
        if worst == "ok":
            worst = "empty"
    else:
        newest = None
        for d in docs:
            ts = (d.to_dict() or {}).get("timestamp")
            if ts and (newest is None or str(ts) > str(newest)):
                newest = ts
        print(f"{sid:<18}{coll:<28}{'ok':<10}{len(docs)} doc(s), newest {newest}")

if not colls:
    print("(no sites resolved — check flood_network.SITES)")

print("-" * 72)
print({
    "ok":    "Collections have data. If the panel still shows NO LIVE DATA, the\n"
             "         problem is downstream: timestamp or stage_ft parsing.",
    "empty": "Connection works; the collections are genuinely empty. Nothing is\n"
             "         writing telemetry yet. This is a data-pipeline gap, not a fault.",
    "error": "The panel CANNOT READ the creek. Absence of data, not absence of\n"
             "         flood — it must never render as NORMAL.",
}[worst])
