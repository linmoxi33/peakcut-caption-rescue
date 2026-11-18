#!/usr/bin/env python3
r"""Reconstruct editable subtitles from PeakCut's progressive word-by-word ASS.

PeakCut (https://github.com/haziqahmedqasmi/PeakCut) burns captions into the
video and leaves the .ass next to it.  ``studio/render.py`` does not emit one
event per line: every word gets up to three overlapping events (waiting /
spoken / already said) at its own ``\pos``, and the group's whole time span is
repeated on each one.  A plain ASS->SRT converter therefore emits fragments,
not lines.  This tool merges events by position, rebuilds whole lines, and
reports timing defects.  Offline, stdlib only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

POS_RE = re.compile(r"\\pos\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
TAG_RE = re.compile(r"\{[^}]*\}")

EXIT_OK, EXIT_DEFECT, EXIT_USAGE = 0, 1, 2


class InputError(Exception):
    pass


def parse_time(value):
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise InputError(f"bad ASS timestamp: {value!r}")
    try:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError:
        raise InputError(f"bad ASS timestamp: {value!r}")
    if hours < 0 or minutes < 0 or seconds < 0:
        raise InputError(f"negative ASS timestamp: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def format_srt_time(seconds):
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600 * 1000)
    m, ms = divmod(ms, 60 * 1000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_vtt_time(seconds):
    return format_srt_time(seconds).replace(",", ".")


def strip_tags(text):
    return TAG_RE.sub("", text).strip()


def parse_ass(text):
    """Return raw caption events: dicts with layer/start/end/style/x/y/text/raw."""
    if "[Events]" not in text:
        raise InputError("no [Events] section: not an ASS subtitle file")
    events = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line[len("Dialogue:"):].lstrip().split(",", 9)
        if len(fields) != 10:
            continue
        layer, start, end, style, _name, _ml, _mr, _mv, _effect, raw = fields
        try:
            s, e = parse_time(start), parse_time(end)
        except InputError:
            continue
        if e <= s:
            events.append({"bad_time": True, "raw": raw, "style": style,
                           "start": s, "end": e})
            continue
        if style.strip().lower() == "hook" or "\\p1" in raw:
            continue  # drawings and the hook box are not spoken captions
        body = strip_tags(raw)
        if not body:
            continue
        pos = POS_RE.search(raw)
        events.append({
            "layer": layer.strip(), "start": s, "end": e,
            "style": style.strip(), "text": body, "raw": raw,
            "x": float(pos.group(1)) if pos else None,
            "y": float(pos.group(2)) if pos else None,
        })
    return events


def merge_words(events):
    """Collapse the up-to-three events per word into one interval per position."""
    by_pos = {}
    for ev in events:
        if ev.get("bad_time"):
            continue
        key = (round(ev["x"] or 0), round(ev["y"] or 0), ev["text"])
        slot = by_pos.setdefault(key, {"text": ev["text"], "x": ev["x"], "y": ev["y"],
                                       "start": ev["start"], "end": ev["end"]})
        slot["start"] = min(slot["start"], ev["start"])
        slot["end"] = max(slot["end"], ev["end"])
    return list(by_pos.values())


def group_lines(words, tolerance=0.12):
    """Words of one PeakCut caption group share the same start/end span."""
    lines = []
    for word in sorted(words, key=lambda w: (w["start"], w["x"] or 0)):
        target = None
        for line in lines:
            if (abs(word["start"] - line["start"]) <= tolerance
                    and abs(word["end"] - line["end"]) <= tolerance
                    and (word["y"] is None or line["y"] is None
                         or abs(word["y"] - line["y"]) <= 2)):
                target = line
                break
        if target is None:
            lines.append({"start": word["start"], "end": word["end"],
                          "y": word["y"], "words": [word]})
        else:
            target["words"].append(word)
            target["start"] = min(target["start"], word["start"])
            target["end"] = max(target["end"], word["end"])
    for line in lines:
        line["words"].sort(key=lambda w: (w["x"] if w["x"] is not None else 0, w["start"]))
        line["text"] = " ".join(w["text"] for w in line["words"]).strip()
    return sorted(lines, key=lambda l: (l["start"], l["end"]))


def find_defects(cues, min_dur=0.4, max_cps=25.0, max_chars=42):
    defects = []
    previous = None
    for cue in cues:
        dur = cue["end"] - cue["start"]
        if not cue["text"]:
            defects.append(_defect("E_EMPTY_TEXT", "error", cue, "cue has no text"))
        if dur < min_dur:
            defects.append(_defect("W_SHORT_CUE", "warning", cue,
                                   f"duration {dur:.2f}s < {min_dur:.2f}s"))
        cps = len(cue["text"]) / dur if dur > 0 else 0.0
        if cps > max_cps:
            defects.append(_defect("W_FAST_READ", "warning", cue,
                                   f"{cps:.1f} chars/s > {max_cps:.0f}"))
        if len(cue["text"]) > max_chars:
            defects.append(_defect("W_LONG_LINE", "warning", cue,
                                   f"{len(cue['text'])} chars > {max_chars}"))
        if previous is not None:
            if cue["start"] < previous["end"] - 0.05:
                defects.append(_defect("E_CUE_OVERLAP", "error", cue,
                                       f"starts {previous['end'] - cue['start']:.2f}s before previous cue ends"))
            if cue["text"] == previous["text"] and cue["text"]:
                defects.append(_defect("W_DUPLICATE_TEXT", "warning", cue, "same text as previous cue"))
        previous = cue
    return defects


def _defect(code, severity, cue, detail):
    return {"code": code, "severity": severity, "cue": cue["index"],
            "start": round(cue["start"], 3), "detail": detail}


def build_cues(events):
    words = merge_words(events)
    lines = group_lines(words)
    cues = []
    for i, line in enumerate(lines, 1):
        cues.append({
            "index": i,
            "start": round(line["start"], 3),
            "end": round(line["end"], 3),
            "text": line["text"],
            "words": [{"text": w["text"], "start": round(w["start"], 3),
                       "end": round(w["end"], 3),
                       "x": None if w["x"] is None else round(w["x"], 1)} for w in line["words"]],
        })
    return cues


def to_srt(cues):
    blocks = []
    for cue in cues:
        blocks.append(f"{cue['index']}\n{format_srt_time(cue['start'])} --> "
                      f"{format_srt_time(cue['end'])}\n{cue['text']}\n")
    return "\n".join(blocks)


def to_vtt(cues):
    blocks = ["WEBVTT\n"]
    for cue in cues:
        blocks.append(f"{format_vtt_time(cue['start'])} --> {format_vtt_time(cue['end'])}\n"
                      f"{cue['text']}\n")
    return "\n".join(blocks)


def report(path, events, cues, defects, min_dur, max_cps, max_chars):
    errors = sum(1 for d in defects if d["severity"] == "error")
    warnings = len(defects) - errors
    return {
        "ok": not defects,
        "source": str(path),
        "input": {
            "caption_events": len([e for e in events if not e.get("bad_time")]),
            "bad_time_events": len([e for e in events if e.get("bad_time")]),
            "words": sum(len(c["words"]) for c in cues),
            "cues": len(cues),
        },
        "thresholds": {"min_duration": min_dur, "max_chars_per_second": max_cps,
                       "max_chars": max_chars},
        "cues": cues,
        "defects": defects,
        "summary": {"errors": errors, "warnings": warnings, "defects": len(defects)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reconstruct whole caption lines from PeakCut's progressive .ass")
    parser.add_argument("input", type=Path, help=".ass file written by PeakCut (studio/render.py)")
    parser.add_argument("--srt", type=Path, help="write restored subtitles as SRT")
    parser.add_argument("--vtt", type=Path, help="write restored subtitles as WebVTT")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    parser.add_argument("--min-dur", type=float, default=0.4)
    parser.add_argument("--max-cps", type=float, default=25.0)
    parser.add_argument("--max-chars", type=int, default=42)
    args = parser.parse_args(argv)

    try:
        if not args.input.is_file():
            raise InputError(f"no such file: {args.input}")
        text = args.input.read_text(encoding="utf-8-sig")
        events = parse_ass(text)
        if not events:
            raise InputError("no caption Dialogue events (only Hook/drawing events?)")
        cues = build_cues(events)
        if not cues:
            raise InputError("events found but no cue could be reconstructed")
        defects = find_defects(cues, args.min_dur, args.max_cps, args.max_chars)
    except (InputError, OSError, UnicodeDecodeError) as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE

    data = report(args.input, events, cues, defects, args.min_dur, args.max_cps, args.max_chars)
    if args.srt:
        args.srt.write_text(to_srt(cues), encoding="utf-8")
    if args.vtt:
        args.vtt.write_text(to_vtt(cues), encoding="utf-8")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"{data['input']['caption_events']} events -> {data['input']['cues']} lines "
              f"({data['input']['words']} words); {data['summary']['errors']} errors, "
              f"{data['summary']['warnings']} warnings")
        for defect in defects:
            print(f"  {defect['severity']}: {defect['code']} cue {defect['cue']} "
                  f"@ {defect['start']:.2f}s - {defect['detail']}")
    return EXIT_OK if not defects else EXIT_DEFECT


if __name__ == "__main__":
    sys.exit(main())