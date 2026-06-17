import json

file_path = "python_node/kaggle_bridge.py"

with open(file_path, "r") as f:
    content = f.read()

# Make sure we start from the clean version
import subprocess
subprocess.run(["git", "restore", file_path])

with open(file_path, "r") as f:
    content = f.read()

with open("/home/titiw/.config/Qoder/SharedClientCache/cli/projects/task-ad291f533bcb4850b526.session.execution.jsonl", "r") as f:
    lines = f.readlines()

for line in lines:
    try:
        data = json.loads(line)
        for part in data.get("parts", []):
            if part.get("type") == "tool_call":
                tc = part.get("data", {})
                if tc.get("name") == "SearchReplace":
                    inp = json.loads(tc.get("input", "{}"))
                    if inp.get("file_path", "").endswith("kaggle_bridge.py"):
                        for rep in inp.get("replacements", []):
                            orig = rep.get("original_text", "")
                            new_t = rep.get("new_text", "")
                            if orig in content:
                                content = content.replace(orig, new_t)
                                print("Applied a replacement")
                            else:
                                print("Failed to apply replacement: original text not found")
                elif tc.get("name") == "WriteFile":
                    inp = json.loads(tc.get("input", "{}"))
                    if inp.get("file_path", "").endswith("kaggle_bridge.py"):
                        content = inp.get("content", "")
                        print("Applied a WriteFile")
    except Exception as e:
        pass

with open("scratch/kaggle_bridge_reconstructed.py", "w") as f:
    f.write(content)

print(f"Reconstructed file has {len(content.splitlines())} lines")
