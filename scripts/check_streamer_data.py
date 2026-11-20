import json
from pathlib import Path

ANN_DIR = Path(r"E:\Bilibili livestream\auto-slicer\data\annotations\-菫時-")
VIDEO_ROOT = Path(r"E:\Bilibili livestream\Bilibili live downloder\3473884--菫時-")

def main():
    json_files = sorted(ANN_DIR.glob("*.segments.json"))
    print(f"找到 {len(json_files)} 個打軸 JSON 檔案。\n")
    
    total_songs = 0
    all_found = True
    matches = []
    
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        media_name = data.get("media_file", "")
        n_songs = data.get("total_songs", len(data.get("segments", [])))
        total_songs += n_songs
        
        # Search for media file under VIDEO_ROOT
        found_path = None
        target_name = Path(media_name).name
        # Direct match or recursive glob
        candidates = list(VIDEO_ROOT.rglob(target_name))
        if candidates:
            found_path = candidates[0]
        else:
            # Try searching with stem
            target_stem = Path(media_name).stem
            candidates = list(VIDEO_ROOT.rglob(f"{target_stem}.*"))
            if candidates:
                found_path = candidates[0]
                
        status = "✅" if found_path and found_path.exists() else "❌ 找不到影片"
        if not found_path or not found_path.exists():
            all_found = False
        print(f"[{status}] {n_songs:02d} 首: {target_name}")
        if found_path:
            print(f"       -> 路徑: {found_path}")
            matches.append((jf, found_path, data))
            
    print(f"\n總計歌曲數量: {total_songs} 首")
    print(f"所有音視訊檔案是否齊全: {'是 (全部就緒)' if all_found else '否 (部分遺失)'}")
    return matches

if __name__ == "__main__":
    main()
