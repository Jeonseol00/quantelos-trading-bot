import json

found_code = ""
with open("/home/titiw/.config/Qoder/SharedClientCache/cli/projects/task-ad291f533bcb4850b526.session.execution.jsonl", "r") as f:
    for line in f:
        try:
            data = json.loads(line)
            for part in data.get("parts", []):
                if part.get("type") == "tool_call":
                    tc = part.get("data", {})
                    if tc.get("name") in ("WriteFile", "SearchReplace", "ViewFile"):
                        inp = json.loads(tc.get("input", "{}"))
                        if inp.get("file_path", "").endswith("kaggle_bridge.py"):
                            print(f"Found tool call {tc.get('name')} for kaggle_bridge.py")
                elif part.get("type") == "tool_response":
                    tr = part.get("data", {})
                    # Need to check if it's the result of ViewFile
                    pass
        except Exception as e:
            pass
