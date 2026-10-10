# Self-auditing Vani

Grades every Vani call against the human rubric, groups failures into root-cause patterns, proposes and tests prompt fixes on a Sarvam test agent, and keeps humans in the loop through a review queue and dashboard.

## Run
```
pip install sarvamai
echo "SARVAM_API_KEY=your_key" > .env
echo "INGEST_TOKEN=choose_a_secret" >> .env
python3 src/server.py        # http://127.0.0.1:8791
```

## Layout
- `src/audit.py` speech-to-text (Sarvam saaras:v4) and judge (sarvam-105b); `src/rubric.py` rubric and severity rules (code, not model)
- `src/insights*.py`, `src/regroup.py` failure grouping; `src/fixes.py`, `src/layer2.py` fix testing on the test agent
- `src/saved_values.py` upper-bound test: audit given saved values taken from human remarks
- `src/server.py` + `web/index.html` the site; `POST /api/ingest` (header `X-Ingest-Token`) accepts calls from a webhook
- `AGENT_SETUP.md` test agent setup

## Not included
Call recordings, human audit labels, the Vani prompt and all outputs (`data/`, `out/`, `cache/`) are internal data and are not in this repo. Without them the site starts empty; upload a call to try it.
