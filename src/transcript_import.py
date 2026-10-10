"""Parse a pasted chat transcript (e.g. from the Sarvam dashboard test chat) into diarized entries."""
import re

BOT = r"bot|vani|agent|assistant|ai|virtual assistant|v"
BUYER = r"buyer|user|customer|caller|human|client|b|you"
LINE = re.compile(r"^\s*(?:\[[^\]]*\]\s*|\d{1,2}:\d{2}(?::\d{2})?\s*[-\u2013]?\s*)?(" + BOT + "|" + BUYER + r")\s*[:\-\u2013\u2014]\s*(.*)$", re.I)


def parse(text):
    turns = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = LINE.match(raw)
        if m:
            who = "bot" if re.fullmatch(BOT, m.group(1).strip(), re.I) else "buyer"
            turns.append([who, m.group(2).strip()])
        elif turns:  # continuation of the previous turn
            turns[-1][1] += " " + raw.strip()
    turns = [(w, t) for w, t in turns if t]
    t, out = 0.0, []
    for who, txt in turns:  # synthetic timestamps: pasted chats carry no timing, so dead air is not assessed
        d = max(1.5, len(txt) / 14)
        out.append({"speaker_id": "0" if who == "bot" else "1", "transcript": txt,
                    "start_time_seconds": round(t, 1), "end_time_seconds": round(t + d, 1)})
        t += d + 0.6
    return out
