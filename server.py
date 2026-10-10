"""Local web app: upload a call recording -> audit report -> suggested fixes -> simulated test audio.

Run:  python3 src/server.py      then open http://127.0.0.1:8765   (binds to localhost only)
"""
import csv
import json
import os
import ipaddress
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import fixer  # noqa: E402
import rubric as R  # noqa: E402
import layer2  # noqa: E402
import agreement  # noqa: E402
import fixes  # noqa: E402
import live  # noqa: E402
import regroup  # noqa: E402
import transcript_import  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS_DIR = os.path.join(ROOT, "out", "jobs")
WEB = os.path.join(ROOT, "web")
os.makedirs(JOBS_DIR, exist_ok=True)

MAX_UPLOAD = 60 * 1024 * 1024
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".webm", ".amr"}
MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".flac": "audio/flac", ".mp4": "audio/mp4", ".webm": "audio/webm", ".amr": "audio/amr"}

JOBS, LOCK = {}, threading.Lock()
SLOTS = threading.Semaphore(2)  # at most 2 audits/tests hit the Sarvam API at once


# ---------- human labels (audit CSV), used when the file is a known audited call ----------
_human = None


def human_rows():
    global _human
    if _human is None:
        _human = {}
        path = os.path.join(ROOT, "data", "raw", "Audit Source of truth.csv")
        if os.path.exists(path):
            r = list(csv.reader(open(path, encoding="utf-8")))
            h = [re.sub(r"\s+", " ", x).strip() for x in r[0]]
            for x in r[1:]:
                if any(x):
                    row = dict(zip(h, x))
                    _human[row.get("Offer ID", "")] = row
    return _human


def human_comparison(offer_id, report):
    row = human_rows().get(offer_id)
    if not row:
        return None
    out, agree, total = [], 0, 0
    for r in report["rows"]:
        hv = row.get(R.HUMAN_COLUMNS.get(r["parameter"], ""), "").strip() or "NA"
        same = hv == r["verdict"] or (hv in ("Pass", "NA") and r["verdict"] in ("Pass", "NA"))
        out.append({"parameter": r["parameter"], "human": hv, "agent": r["verdict"], "match": same})
        agree, total = agree + same, total + 1
    return {"offer_id": offer_id, "rows": out, "agree": agree, "total": total,
            "human_overall": row.get("Overall Quality", ""), "human_call_quality": row.get("Call Quality", ""),
            "human_remarks": row.get("Overall Remarks", "")[:400]}


# ---------- jobs ----------
def save(job):
    with LOCK:
        json.dump(job, open(os.path.join(JOBS_DIR, job["id"], "job.json"), "w"), ensure_ascii=False)


def set_status(job, status, msg=""):
    job["status"], job["message"] = status, msg
    save(job)


def run_audit(job, path):
    try:
        with SLOTS:
            rep = audit.audit_audio(path, job["filename"], lambda s, m: set_status(job, s, m), recorded=job.get("recorded_values"))
            job["report"] = rep
            m = re.search(r"\d{9,}", job["filename"])
            if m:
                job["human"] = human_comparison(m.group(0), rep)
            set_status(job, "suggest", "Working out root causes and prompt fixes")
            try:
                job["issues"] = fixer.suggest(rep)
            except Exception as e:  # the audit is still valid without suggestions
                job["issues"] = []
                job["suggest_error"] = str(e)
        set_status(job, "done", "")
        threading.Thread(target=assign_pending, daemon=True).start()
    except Exception as e:
        traceback.print_exc()
        job["error"] = str(e)
        set_status(job, "error", str(e))


def run_test(job, idx, runs):
    key = str(idx)
    t = job["tests"][key]
    controls = [j["report"] for j in list(JOBS.values())
                if j["id"] != job["id"] and j.get("report") and j["report"]["overall"] == "Pass"
                and j["report"]["source"] != "simulated"][:2]

    def prog(done, total, msg):
        t.update(message=msg, done=done, total=total)
        save(job)
    try:
        with SLOTS:
            res = layer2.run_layer2(job["report"], job["issues"][idx], controls, runs, prog)
        t.update(status="done", result=res, message="", control_names=len(controls))
    except Exception as e:
        traceback.print_exc()
        t.update(status="error", message=str(e))
    save(job)


def render_cell(job, idx, cell):
    t = job["tests"][str(idx)]
    c = t["result"]["cells"][cell]
    d = os.path.join(JOBS_DIR, job["id"], f"test{idx}")
    os.makedirs(d, exist_ok=True)
    name = f"cell{cell}.wav"
    try:
        layer2.render_audio(c["conversation"], os.path.join(d, name))
        c["audio"] = name
    except Exception as e:
        c["audio_error"] = str(e)[:200]
    c["rendering"] = False
    save(job)


def run_text_audit(job, entries):
    try:
        with SLOTS:
            set_status(job, "judge", "Auditing against the rubric")
            rep = audit.build_report(entries, audit.judge_twice(entries), job["filename"], "pasted transcript")
            job["report"] = rep
            m = re.search(r"\d{9,}", job["filename"])
            if m:
                job["human"] = human_comparison(m.group(0), rep)
            set_status(job, "suggest", "Working out root causes and prompt fixes")
            try:
                job["issues"] = fixer.suggest(rep)
            except Exception as e:
                job["issues"] = []
                job["suggest_error"] = str(e)
        set_status(job, "done", "")
        threading.Thread(target=assign_pending, daemon=True).start()
    except Exception as e:
        traceback.print_exc()
        job["error"] = str(e)
        set_status(job, "error", str(e))



ASSIGN_LOCK = threading.Lock()
REGROUP = {"status": "idle", "message": "", "started": None, "finished": None}


def assign_pending():
    """Match the problems of every audited call that has not been matched yet to a pattern (one call at a time)."""
    with ASSIGN_LOCK:
        for j in list(JOBS.values()):
            if live.counted(j) and "live" not in j:
                live.assign_job(j)
                save(j)


def run_regroup():
    REGROUP.update(status="running", message="Collecting problems", started=int(time.time()), finished=None)
    try:
        assign_pending()
        with SLOTS:
            out = regroup.run(live.collect_issues(list(JOBS.values())), lambda m: REGROUP.update(message=m))
        REGROUP.update(status="done", message=f"{len(out['clusters'])} patterns from {out['issues']} problems", finished=int(time.time()))
    except Exception as e:
        traceback.print_exc()
        REGROUP.update(status="error", message=str(e)[:200], finished=int(time.time()))


def run_reaudit(job):
    try:
        with SLOTS:
            set_status(job, "judge", "Running the audit again")
            entries = job["report"]["transcript"]
            src = job["report"].get("source", "upload")
            rep = audit.build_report(entries, audit.judge_twice(entries, job.get("recorded_values")), job["filename"], src, job.get("recorded_values"))
            job["report"] = rep
            job.pop("live", None)
            job.pop("review", None)
            set_status(job, "suggest", "Working out root causes and prompt fixes")
            try:
                job["issues"] = fixer.suggest(rep)
            except Exception as e:
                job["issues"], job["suggest_error"] = [], str(e)
        set_status(job, "done", "")
        threading.Thread(target=assign_pending, daemon=True).start()
    except Exception as e:
        traceback.print_exc()
        job["error"] = str(e)
        set_status(job, "error", str(e))


def new_text_job(filename, entries, kind="production"):
    jid = uuid.uuid4().hex[:12]
    os.makedirs(os.path.join(JOBS_DIR, jid))
    job = {"id": jid, "filename": filename, "audio": None, "created": int(time.time()), "status": "queued",
           "message": "", "tests": {}, "kind": kind}
    JOBS[jid] = job
    save(job)
    threading.Thread(target=run_text_audit, args=(job, entries), daemon=True).start()
    return job


def ingest_token():
    if os.environ.get("INGEST_TOKEN"):
        return os.environ["INGEST_TOKEN"]
    fp = os.path.join(ROOT, ".env")
    for line in (open(fp) if os.path.exists(fp) else []):
        if line.startswith("INGEST_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def fetch_audio(url):
    """Download a call recording from an https URL. Refuses private and loopback addresses and oversized files."""
    pu = urlparse(url)
    if pu.scheme != "https" or not pu.hostname:
        raise ValueError("audio_url must be an https URL")
    for fam, _, _, _, addr in socket.getaddrinfo(pu.hostname, pu.port or 443):
        ip = ipaddress.ip_address(addr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("audio_url points to a private address")
    import urllib.request as ur
    with ur.urlopen(url, timeout=60) as r:
        data = r.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ValueError("audio file too large (limit 60 MB)")
    if len(data) < 2000:
        raise ValueError("audio file is empty or too small")
    return data


def ingest(call_id, data, recorded, count, meta, ext=".mp3"):  # `count` is kept for old callers; every call counts
    for j in JOBS.values():
        if j.get("call_id") == call_id:
            return j, True
    name = re.sub(r"[^A-Za-z0-9._-]", "_", call_id)[:80] + ext
    return new_job(name, data, "production", {"call_id": call_id, "recorded_values": recorded or None, "meta": meta or {}}), False


def inbox_loop():
    """Watch data/inbox: every new audio file (with an optional <name>.json sidecar) becomes a production call."""
    inbox = os.path.join(ROOT, "data", "inbox")
    done = os.path.join(inbox, "done")
    os.makedirs(done, exist_ok=True)
    while True:
        try:
            for f in sorted(os.listdir(inbox)):
                base, ext = os.path.splitext(f)
                if ext.lower() not in AUDIO_EXT:
                    continue
                fp = os.path.join(inbox, f)
                if time.time() - os.path.getmtime(fp) < 3:  # still being written
                    continue
                side = {}
                sp = os.path.join(inbox, base + ".json")
                if os.path.exists(sp):
                    side = json.load(open(sp))
                ingest(str(side.get("call_id") or base), open(fp, "rb").read(), side.get("recorded_values"), side.get("count", True),
                       side.get("meta"), ext.lower())
                shutil.move(fp, os.path.join(done, f))
                if os.path.exists(sp):
                    shutil.move(sp, os.path.join(done, base + ".json"))
        except Exception:
            traceback.print_exc()
        time.sleep(10)


def new_job(filename, data, kind="production", extra=None):
    jid = uuid.uuid4().hex[:12]
    d = os.path.join(JOBS_DIR, jid)
    os.makedirs(d)
    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(d, "original" + ext)
    open(path, "wb").write(data)
    job = {"id": jid, "filename": filename, "audio": "original" + ext, "created": int(time.time()), "status": "queued",
           "message": "", "tests": {}, "kind": kind, **(extra or {})}
    JOBS[jid] = job
    save(job)
    threading.Thread(target=run_audit, args=(job, path), daemon=True).start()
    return job


def load_jobs():
    for jid in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, jid, "job.json")
        if os.path.exists(p):
            try:
                j = json.load(open(p))
                if j["status"] not in ("done", "error"):  # interrupted by a restart
                    j["status"], j["error"] = "error", "interrupted by server restart"
                for t in j.get("tests", {}).values():
                    if t["status"] == "running":
                        t["status"], t["message"] = "error", "interrupted by server restart"
                    for c in (t.get("result") or {}).get("cells", []):
                        c["rendering"] = False
                j["kind"] = "production"
                JOBS[jid] = j
            except Exception:
                pass


def summary(j):
    rep = j.get("report")
    return {"id": j["id"], "filename": j["filename"], "created": j["created"], "status": j["status"],
            "overall": rep["overall"] if rep else None, "flagged": rep["risk"]["flag"] if rep else None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_ranged(self, path, ctype):
        """Serve a file with HTTP range support, so a browser can jump to the middle of an audio file."""
        data = open(path, "rb").read()
        n = len(data)
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                start = int(a) if a else max(0, n - int(b))
                end = int(b) if (a and b) else n - 1
            except ValueError:
                start, end = 0, n - 1
            end = min(end, n - 1)
            if start > end or start >= n:
                return self.send(416, b"", ctype, {"Content-Range": f"bytes */{n}"})
            return self.send(206, data[start:end + 1], ctype, {"Content-Range": f"bytes {start}-{end}/{n}", "Accept-Ranges": "bytes"})
        return self.send(200, data, ctype, {"Accept-Ranges": "bytes"})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("file too large (limit 60 MB)")
        return self.rfile.read(n)

    def _authed(self, open_paths=()):
        """Shared-password gate (HTTP Basic) when SITE_PASSWORD is set; any username. Paths in open_paths use their own token."""
        pw = os.environ.get("SITE_PASSWORD")
        if not pw or urlparse(self.path).path in open_paths:
            return True
        import base64
        import hmac
        try:
            given = base64.b64decode(self.headers.get("Authorization", "")[6:]).decode().partition(":")[2]
        except Exception:
            given = ""
        if hmac.compare_digest(given.encode(), pw.encode()):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Vani audit"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._authed():
            return
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if not parts:
            return self.send(200, open(os.path.join(WEB, "index.html"), "rb").read(), "text/html; charset=utf-8")
        if parts == ["api", "jobs"]:
            return self.send(200, sorted((summary(j) for j in JOBS.values()), key=lambda x: -x["created"]))
        if parts == ["api", "live"]:
            agg = live.aggregate(list(JOBS.values()))
            if agg["pending"] and not ASSIGN_LOCK.locked():
                threading.Thread(target=assign_pending, daemon=True).start()
            return self.send(200, agg)
        if parts == ["api", "fixes"]:
            out = []
            for c in fixes.catalog():
                d = fixes.public(fixes.load(c["id"]))
                d["pack"] = None if not d["pack"] else {"scripts": len(d["pack"]["scripts"])}
                d["runs"] = len(d["runs"])
                out.append(d)
            return self.send(200, out)
        if len(parts) == 3 and parts[:2] == ["api", "fix"]:
            d = fixes.load(parts[2])
            return self.send(200, fixes.public(d)) if d else self.send(404, {"error": "unknown fix"})
        if len(parts) == 4 and parts[:2] == ["api", "fix"] and parts[3] == "prompt":
            txt = fixes.patched_text(parts[2])
            if txt is None:
                return self.send(404, {"error": "build the patched prompt first"})
            return self.send(200, txt.encode("utf-8"), "text/plain; charset=utf-8",
                             {"Content-Disposition": f'attachment; filename="vani_prompt_patched_{parts[2]}.txt"'})
        if parts == ["api", "review", "queue"]:
            pats = live.patterns()
            ts = re.compile(r"\[(\d+(?:\.\d+)?)s\]")
            calls = []
            for j in sorted(JOBS.values(), key=lambda x: -x["created"]):
                if not live.counted(j):
                    continue
                rep = j["report"]
                rows = {r["parameter"]: r for r in rep["rows"]}
                items = []
                for i, it in enumerate((j.get("live") or {}).get("items", [])):
                    r = rows.get(it["parameter"], {})
                    m = ts.search((r.get("evidence") or "") + " " + (r.get("reason") or ""))
                    items.append({"i": i, **it, "evidence": r.get("evidence", ""), "reason": r.get("reason", ""),
                                  "t": float(m.group(1)) if m else None})
                calls.append({"id": j["id"], "filename": j["filename"], "created": j["created"], "overall": rep["overall"],
                              "flags": rep["risk"]["reasons"], "items": items, "review": j.get("review"),
                              "needs_review": live.needs_review(j), "assigned": "live" in j,
                              "audio": j.get("audio"), "recorded": j.get("recorded_values"),
                              "transcript": [{"s": e["start_time_seconds"], "e": e["end_time_seconds"], "bot": str(e["speaker_id"]) == "0",
                                              "x": e["transcript"]} for e in rep["transcript"]],
                              "facts": rep["facts"]})
            calls.sort(key=lambda c: (not c["needs_review"], c["review"] is not None, c["overall"] != "Fatal", -c["created"]))
            return self.send(200, {"patterns": [{"title": p["title"]} for p in pats], "params": R.PARAMS + ["Dead air"], "calls": calls})
        if parts == ["api", "review", "stats"]:
            return self.send(200, live.review_stats(list(JOBS.values())))
        if parts == ["api", "regroup"]:
            return self.send(200, REGROUP)
        if parts == ["api", "agreement"]:
            a = agreement.compute()
            return self.send(200, a) if a else self.send(404, {"error": "no agreement data"})
        if parts == ["api", "failed30"]:
            fp = os.path.join(ROOT, "out", "failed30", "summary.json")
            return self.send(200, open(fp, "rb").read()) if os.path.exists(fp) else self.send(404, {"error": "none"})
        if parts == ["api", "insights"]:
            src = parse_qs(u.query).get("source", ["manual"])[0]
            name = {"manual": "insights_manual.json", "live": "insights_live.json"}.get(src, "insights.json")
            fp = os.path.join(ROOT, "out", "insights", name)
            return self.send(200, open(fp, "rb").read()) if os.path.exists(fp) else self.send(404, {"error": "no insights yet"})
        if parts == ["api", "insights", "sheet"]:
            fp = os.path.join(ROOT, "out", "insights", "grouping_sheet.csv")
            return self.send(200, open(fp, "rb").read(), "text/csv; charset=utf-8") if os.path.exists(fp) else self.send(404, {"error": "no sheet"})
        if parts == ["api", "batch"]:
            bd = os.path.join(ROOT, "out", "batch")
            if not os.path.exists(os.path.join(bd, "summary.json")):
                return self.send(404, {"error": "no batch run yet"})
            return self.send(200, {"summary": json.load(open(os.path.join(bd, "summary.json"))),
                                   "results": json.load(open(os.path.join(bd, "results.json")))})
        if parts == ["api", "samples"]:
            rec = os.path.join(ROOT, "data", "recordings")
            return self.send(200, [f for f in sorted(os.listdir(rec)) if f.endswith(".mp3")] if os.path.isdir(rec) else [])
        if len(parts) == 3 and parts[:2] == ["api", "job"] and re.fullmatch(r"[0-9a-f]{12}", parts[2]):
            j = JOBS.get(parts[2])
            return self.send(200, j) if j else self.send(404, {"error": "not found"})
        if len(parts) == 4 and parts[:2] == ["api", "audio"] and re.fullmatch(r"[0-9a-f]{12}", parts[2]):
            rel = parts[3] if parts[3] == JOBS.get(parts[2], {}).get("audio") else None
            q = parse_qs(u.query).get("test", [None])[0]
            base = os.path.join(JOBS_DIR, parts[2])
            if q is not None and re.fullmatch(r"\d+", q) and re.fullmatch(r"cell\d+\.wav", parts[3]):
                p = os.path.join(base, f"test{q}", parts[3])
            elif rel:
                p = os.path.join(base, rel)
            else:
                return self.send(404, {"error": "not found"})
            if not os.path.exists(p):
                return self.send(404, {"error": "not found"})
            return self.serve_ranged(p, MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed(("/api/ingest",)):
            return
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            if parts == ["api", "audit"]:
                name = os.path.basename(parse_qs(u.query).get("name", ["call.mp3"])[0]) or "call.mp3"
                if os.path.splitext(name)[1].lower() not in AUDIO_EXT:
                    return self.send(400, {"error": "unsupported file type; upload an audio file (mp3, wav, m4a, ...)"})
                data = self.body()
                if not data:
                    return self.send(400, {"error": "empty file"})
                return self.send(200, {"id": new_job(name, data)["id"]})
            if parts == ["api", "ingest"]:
                tok = ingest_token()
                if not tok or self.headers.get("X-Ingest-Token", "") != tok:
                    return self.send(401, {"error": "missing or wrong X-Ingest-Token"})
                req = json.loads(self.body() or b"{}")
                cid = str(req.get("call_id") or "").strip()
                if not cid or not req.get("audio_url"):
                    return self.send(400, {"error": "call_id and audio_url are required"})
                try:
                    data = fetch_audio(str(req["audio_url"]))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                rec = req.get("recorded_values") if isinstance(req.get("recorded_values"), dict) else None
                j, dup = ingest(cid, data, rec, req.get("count", True), req.get("meta") if isinstance(req.get("meta"), dict) else None)
                return self.send(200, {"id": j["id"], "duplicate": dup})
            if len(parts) == 4 and parts[:2] == ["api", "fix"]:
                fid, act = parts[2], parts[3]
                req = json.loads(self.body() or b"{}")
                if not fixes.load(fid):
                    return self.send(404, {"error": "unknown fix"})
                try:
                    if act == "patch":
                        fixes.build_patch(fid)
                    elif act == "pack":
                        with SLOTS:
                            fixes.make_pack(fid, str(req.get("product") or fixes.DEFAULT_PRODUCT)[:80], str(req.get("options") or fixes.DEFAULT_OPTIONS)[:400])
                    elif act == "run":
                        with SLOTS:
                            fixes.add_run(fid, req.get("arm"), req.get("script"), str(req.get("text", "")), str(req.get("by", "")))
                    elif act == "clear":
                        fixes.clear_runs(fid, req.get("arm"))
                    elif act == "decision":
                        fixes.decide(fid, req.get("status"), req.get("by"), req.get("note"))
                    else:
                        return self.send(404, {"error": "not found"})
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                return self.send(200, fixes.public(fixes.load(fid)))
            if len(parts) == 3 and parts[:2] == ["api", "review"] and re.fullmatch(r"[0-9a-f]{12}", parts[2]):
                j = JOBS.get(parts[2])
                if not j or not live.counted(j):
                    return self.send(404, {"error": "call not found"})
                req = json.loads(self.body() or b"{}")
                n_items = len((j.get("live") or {}).get("items", []))
                n_pat = len(live.patterns())
                verdicts = ("Fatal", "Non-Fatal", "Pass")
                items = {}
                for k, d in (req.get("items") or {}).items():
                    if not (k.isdigit() and int(k) < n_items):
                        continue
                    a = d.get("action")
                    if a not in ("confirm", "reassign", "not_a_problem"):
                        continue
                    pat = d.get("pattern")
                    items[k] = {"action": a, "pattern": pat if isinstance(pat, int) and -1 <= pat < n_pat else None,
                                "note": str(d.get("note", ""))[:300], "reason": str(d.get("reason", ""))[:80],
                                "verdict": d.get("verdict") if d.get("verdict") in ("Fatal", "Non-Fatal") else None}
                added = []
                for ad in (req.get("added") or [])[:10]:
                    if ad.get("parameter") in R.PARAMS + ["Dead air"] and ad.get("verdict") in ("Fatal", "Non-Fatal"):
                        pat = ad.get("pattern")
                        added.append({"parameter": ad["parameter"], "verdict": ad["verdict"],
                                      "pattern": pat if isinstance(pat, int) and -1 <= pat < n_pat else None,
                                      "note": str(ad.get("note", ""))[:300]})
                overall = req.get("overall") if req.get("overall") in verdicts else None
                j["review"] = {"reviewer": str(req.get("reviewer", "")).strip()[:60] or "Anonymous", "at": int(time.time()),
                               "overall": overall, "items": items, "added": added}
                save(j)
                return self.send(200, {"ok": True, "stats": live.review_stats(list(JOBS.values()))})
            if len(parts) == 4 and parts[:2] == ["api", "job"] and parts[3] == "reaudit" and re.fullmatch(r"[0-9a-f]{12}", parts[2]):
                j = JOBS.get(parts[2])
                if not j or j.get("status") != "done" or not j.get("report"):
                    return self.send(400, {"error": "this call has no finished audit to re-run"})
                threading.Thread(target=run_reaudit, args=(j,), daemon=True).start()
                return self.send(200, {"ok": True})
            if parts == ["api", "regroup", "start"]:
                if REGROUP["status"] == "running":
                    return self.send(409, {"error": "already running"})
                threading.Thread(target=run_regroup, daemon=True).start()
                return self.send(200, {"ok": True})
            if parts == ["api", "transcript"]:
                req = json.loads(self.body() or b"{}")
                name = (os.path.basename(str(req.get("name", "")).strip()) or "pasted transcript")[:80]
                entries = transcript_import.parse(str(req.get("text", "")))
                if len(entries) < 2:
                    return self.send(400, {"error": "Could not find at least two turns. Start each line with Vani: / Buyer: (or Agent: / User:)."})
                return self.send(200, {"id": new_text_job(name, entries)["id"], "turns": len(entries)})
            if parts == ["api", "sample"]:
                name = os.path.basename(json.loads(self.body() or b"{}").get("name", ""))
                p = os.path.join(ROOT, "data", "recordings", name)
                if not name.endswith(".mp3") or not os.path.exists(p):
                    return self.send(404, {"error": "unknown sample"})
                return self.send(200, {"id": new_job(name, open(p, "rb").read())["id"]})
            if len(parts) == 4 and parts[:2] == ["api", "job"] and parts[3] in ("test", "render"):
                j = JOBS.get(parts[2])
                req = json.loads(self.body() or b"{}")
                idx = int(req.get("issue", -1))
                if not j or j["status"] != "done" or not 0 <= idx < len(j.get("issues", [])):
                    return self.send(400, {"error": "bad job or issue"})
                if parts[3] == "test":
                    if j["tests"].get(str(idx), {}).get("status") == "running":
                        return self.send(409, {"error": "already running"})
                    runs = max(1, min(3, int(req.get("runs", 1))))
                    j["tests"][str(idx)] = {"status": "running", "message": "starting", "done": 0, "total": 0, "runs": runs}
                    save(j)
                    threading.Thread(target=run_test, args=(j, idx, runs), daemon=True).start()
                    return self.send(200, {"ok": True})
                t = j["tests"].get(str(idx), {})
                cell = int(req.get("cell", -1))
                if t.get("status") != "done" or not 0 <= cell < len(t["result"]["cells"]) or not t["result"]["cells"][cell]["conversation"]:
                    return self.send(400, {"error": "bad cell"})
                t["result"]["cells"][cell]["rendering"] = True
                save(j)
                threading.Thread(target=render_cell, args=(j, idx, cell), daemon=True).start()
                return self.send(200, {"ok": True})
        except ValueError as e:
            return self.send(413, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": str(e)})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    load_jobs()
    threading.Thread(target=assign_pending, daemon=True).start()
    threading.Thread(target=inbox_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8791))
    host = os.environ.get("HOST", "127.0.0.1")  # 0.0.0.0 inside a container
    print(f"Vani audit site: http://{host}:{port}")
    ThreadingHTTPServer((host, port), H).serve_forever()
