import json
from pathlib import Path

class DBCParser:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def parse(self) -> dict:
        if not self.file_path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {self.file_path}")
        
        with open(self.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    
if __name__ == "__main__":
    parser = DBCParser("sample_dbc.json")
    parsed = parser.parse()
    print(parsed)