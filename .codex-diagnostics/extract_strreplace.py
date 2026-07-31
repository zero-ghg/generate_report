import json

path = r"C:\Users\69118\.cursor\projects\d-zero-work-XM-generate-report\agent-transcripts\a1b09f4c-7a08-4bc4-94a7-6815af284b0d\subagents\65ebad71-9031-439f-8d4c-96938e082943.jsonl"
out = r"D:\zero\work\XM\generate_report\.codex-diagnostics\strreplace_extract.json"
results = []
with open(path, "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        try:
            obj = json.loads(line)
        except Exception as e:
            print(f"parse fail line {i}: {e}")
            continue
        msg = obj.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use" and part.get("name") == "StrReplace":
                inp = part.get("input", {})
                p = inp.get("path", "")
                if "LegendDesigner.tsx" in p or "styles.css" in p:
                    results.append(
                        {
                            "line": i,
                            "path": p,
                            "old_string": inp.get("old_string", ""),
                            "new_string": inp.get("new_string", ""),
                            "replace_all": inp.get("replace_all", False),
                        }
                    )

print(f"found {len(results)} StrReplace")
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

for i, r in enumerate(results):
    print("---")
    fname = r["path"].replace("\\", "/").split("/")[-1]
    print(f"#{i+1} line={r['line']} path={fname}")
    print(f"old_len={len(r['old_string'])} new_len={len(r['new_string'])}")
    print("OLD_HEAD:", repr(r["old_string"][:200]))
    print("NEW_HEAD:", repr(r["new_string"][:200]))
