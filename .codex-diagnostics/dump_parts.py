import json
from pathlib import Path

src = Path(r"D:\zero\work\XM\generate_report\.codex-diagnostics\strreplace_extract.json")
data = json.loads(src.read_text(encoding="utf-8"))
outdir = Path(r"D:\zero\work\XM\generate_report\.codex-diagnostics\strreplace_parts")
outdir.mkdir(exist_ok=True)

for i, r in enumerate(data, 1):
    fname = r["path"].replace("\\", "/").split("/")[-1]
    (outdir / f"{i:02d}_{fname}_OLD.txt").write_text(r["old_string"], encoding="utf-8")
    (outdir / f"{i:02d}_{fname}_NEW.txt").write_text(r["new_string"], encoding="utf-8")
    print(f"wrote #{i} {fname} old={len(r['old_string'])} new={len(r['new_string'])}")

# Also check for handlePresetLibraryDragOver / useEffect related to loadPreset
keywords = [
    "handlePresetLibraryDragOver",
    "loadPresetLegendPreviews",
    "yieldToBrowser",
    "dataTransferHasType",
    "presetDragGhostRef",
    "useEffect",
]
for i, r in enumerate(data, 1):
    blob = r["old_string"] + "\n" + r["new_string"]
    hits = [k for k in keywords if k in blob]
    if hits:
        print(f"#{i} mentions: {hits}")
