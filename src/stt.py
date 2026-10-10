"""Step 1: diarized transcription of the pilot calls via Sarvam batch STT.

Run:  PYTHONPATH=.pylibs python3 src/stt.py
Results are cached in cache/stt/<offer_id>.json; calls already cached are skipped.
"""
import json
import os
import shutil
import sys
import tempfile

from sarvamai import SarvamAI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, "data", "recordings")
CACHE = os.path.join(ROOT, "cache", "stt")


def load_key():
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith("SARVAM_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("SARVAM_API_KEY missing in .env")


def main():
    os.makedirs(CACHE, exist_ok=True)
    calls = [c["offer_id"] for c in json.load(open(os.path.join(ROOT, "data", "pilot_calls.json")))]
    todo = [o for o in calls if not os.path.exists(os.path.join(CACHE, f"{o}.json"))]
    if not todo:
        print("all calls cached; nothing to do")
        return
    print("transcribing:", todo)

    client = SarvamAI(api_subscription_key=load_key())
    job = client.speech_to_text_job.create_job(
        model="saaras:v4",
        mode="transcribe",
        language_code="unknown",
        with_diarization=True,
        num_speakers=2,
    )
    job.upload_files(file_paths=[os.path.join(REC, f"{o}.mp3") for o in todo])
    job.start()
    job.wait_until_complete()

    res = job.get_file_results()
    for f in res["failed"]:
        print("FAILED", f["file_name"], f["error_message"])
    if not res["successful"]:
        sys.exit(1)

    out = tempfile.mkdtemp()
    job.download_outputs(output_dir=out)
    for name in os.listdir(out):
        # output files are named after the input, e.g. 144801537785.mp3.json
        offer = name.split(".")[0]
        shutil.copy(os.path.join(out, name), os.path.join(CACHE, f"{offer}.json"))
        print("cached", offer)


if __name__ == "__main__":
    main()
