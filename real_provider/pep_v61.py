from pathlib import Path

class UnifiedPEPV61:
    WRITERS={"frontend","backend"}

    def __init__(self,root):
        self.root=Path(root).resolve()

    def write_text(self,role,path,content,allowed_paths):
        if role not in self.WRITERS:
            return {"status":"DENIED","reason":"role_not_writer"}
        if path not in set(allowed_paths):
            return {"status":"DENIED","reason":"path_not_allowlisted"}
        rel=Path(path)
        if rel.is_absolute() or ".." in rel.parts:
            return {"status":"DENIED","reason":"invalid_path"}
        target=(self.root/rel).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return {"status":"DENIED","reason":"path_escape"}
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding="utf-8")
        return {"status":"WRITTEN","path":path}
