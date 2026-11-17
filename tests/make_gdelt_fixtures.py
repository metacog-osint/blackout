"""Record real GDELT GKG excerpts for tests/test_public.py (TestGdeltHeadlines):

    python tests/make_gdelt_fixtures.py --calibration <folder>

For each scenario, every GKG 15-minute file of its 6-hour window is read from data.gdeltproject.org and cut down
before it is written: only rows whose headline matches a watched name with an outage word, plus up to `OTHERS` rows
per file whose headline names a watched name without one (real near misses), and in each row only the five columns
Blackout reads (record id, date, source collection, outlet domain, URL) and the <PAGE_TITLE> of the extras column; the
other 21 columns (names, places, themes, quotations from the articles) are emptied. Each excerpt is a zip shaped like
GDELT's own. The 7 days of files before the window come from tools/gdelt_calibrate.py's per-file cache (--calibration),
reduced to what the lane carries for files that old (a count per name), as the scenario's carry. A file GDELT answers
404 for is listed as missing. The current lastupdate.txt is kept as a sample for the parser."""
import argparse
import io
import json
import sys
import urllib.error
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import gdelt  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "gdelt"
OTHERS = 2
# name: (run time, GDELT's newest file at that run, the surges expected from the verified events)
SCENARIOS = {
    # 16 Nov 2026: Chase, Bank of America and Wells Fargo from about 16:00 UTC (CNN, Fortune, Detroit News); United's
    # system outage in the evening (The National: over 90 min; 851 delayed, 51 cancelled by midnight ET).
    "banks-united": ("2026-11-17T01:45:00Z", "2026-11-17T01:30:00Z"),
    # 13 Nov 2026: the National Weather Service said users might not reach its products (Reuters/US News, 23:00 UTC
    # on), the evening of the FAA's nationwide ground stop.
    "nws": ("2026-11-14T01:45:00Z", "2026-11-14T01:30:00Z"),
}
# The verified events each window holds (tools/gdelt_calibrate.py flagged these runs, and only these, on 17 Nov 2026).
EXPECT = {"banks-united": ["bank-of-america", "chase", "united-airlines", "us-bank", "wells-fargo"],
          "nws": ["faa", "nws"]}


def excerpt(text: str) -> tuple[str, list]:
    keep, cases, others = [], [], 0
    for row in text.split("\n"):
        cols = row.split("\t")
        if len(cols) < 27 or "<PAGE_TITLE>" not in cols[26]:
            continue
        title = gdelt.title_of(cols[26])
        hit = gdelt.match(title)
        named = [k for k, _, rx in gdelt._PATTERNS if rx.search(title)]
        if not hit and not (named and others < OTHERS):
            continue
        if not hit:
            others += 1
        m = gdelt.TITLE.search(cols[26])
        out = cols[:5] + [""] * 21 + [m.group(0)]
        keep.append("\t".join(out))
        cases += [[k, title] for k in hit]
    return "\n".join(keep) + ("\n" if keep else ""), cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration", required=True, help="tools/gdelt_calibrate.py's --cache folder")
    a = ap.parse_args()
    cal = Path(a.calibration)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"scenarios": {}, "match_cases": []}
    sample = gdelt._get(gdelt.LASTUPDATE)
    (OUT / "lastupdate_sample.txt").write_bytes(sample)
    manifest["lastupdate_sample"] = "lastupdate_sample.txt"
    cases = []
    for name, (run, newest_s) in SCENARIOS.items():
        newest = datetime.strptime(newest_s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        window = gdelt.stamps(newest - gdelt.STEP * 23, newest)
        files, missing = {}, []
        for s in sorted(window):
            url = gdelt.file_url(s)
            try:
                body = gdelt._get(url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    missing.append(s)
                    continue
                raise
            z = zipfile.ZipFile(io.BytesIO(body))
            inner = z.namelist()[0]
            text, c = excerpt(z.read(inner).decode("utf-8", "replace"))
            cases += c
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
                out.writestr(zipfile.ZipInfo(inner, date_time=(1980, 1, 1, 0, 0, 0)), text)
            fname = f"{s}.gkg.excerpt.zip"
            (OUT / fname).write_bytes(buf.getvalue())
            files[url] = fname
        base_last = newest - gdelt.STEP * 24
        baseline = gdelt.stamps(base_last - gdelt.STEP * (7 * 96 - 1), base_last)
        carried, gone = {}, []
        for s in baseline:
            p = cal / f"{s}.json"
            if not p.exists():
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("_missing"):
                gone.append(s)
                continue
            carried[s] = gdelt._counts_only({k: [x[:2] for x in v] for k, v in d.items()})
        carry = gdelt.carry_doc(carried, gone, datetime.strptime(run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
        carry_name = f"carry_{name}.json"
        (OUT / carry_name).write_text(json.dumps(carry, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
        manifest["scenarios"][name] = {"run": run, "newest": newest_s, "files": files, "window_missing": missing,
                                       "carry": carry_name, "expect_surges": EXPECT[name]}
        print(name, len(files), "files,", len(missing), "missing,", len(carried), "baseline files carried")
    seen, uniq = set(), []
    for k, t in cases:
        if t not in seen:
            seen.add(t)
            uniq.append([k, t])
    manifest["match_cases"] = uniq[:16]
    manifest["note"] = (
        "Real GKG 2.1 files from https://data.gdeltproject.org/gdeltv2/ (the GDELT Project, unrestricted use with "
        "citation, https://www.gdeltproject.org/), recorded by tests/make_gdelt_fixtures.py and cut to excerpts: only "
        "rows whose headline names a watched service (with an outage word, plus up to 2 near misses per file), and in "
        "each row only columns 0-4 and the extras column's <PAGE_TITLE>; the other columns are emptied. The headlines "
        "stay in these test files only; Blackout never publishes them. carry_*.json: the 7 days of files before each "
        "window as the lane carries files that old (a count per name), reduced from tools/gdelt_calibrate.py's "
        "reading of every file.")
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                       newline="\n")


if __name__ == "__main__":
    main()
