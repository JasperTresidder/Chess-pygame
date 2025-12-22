import json
import os
import sys

def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parquet_path = os.path.join(root, 'lit', 'database', 'chess-openings', 'data', 'train-00000-of-00001.parquet')
    out_path = os.path.join(root, 'lit', 'database', 'chess-openings', 'openings_epd.json')

    if not os.path.exists(parquet_path):
        print(f"Missing parquet: {parquet_path}")
        return 2

    try:
        import pyarrow.parquet as pq
    except Exception as e:
        print("pyarrow is required to build openings_epd.json")
        print(e)
        return 3

    table = pq.read_table(parquet_path, columns=['epd', 'name', 'eco', 'eco-volume'])
    epd = table.column('epd').to_pylist()
    name = table.column('name').to_pylist()
    eco = table.column('eco').to_pylist()
    vol = table.column('eco-volume').to_pylist()

    out: dict[str, dict] = {}
    for i in range(len(epd)):
        k = str(epd[i] or '').strip()
        if not k:
            continue
        nm = str(name[i] or '').strip()
        if not nm:
            continue
        # Prefer the first/shortest name if duplicates exist.
        if k in out:
            try:
                if len(nm) >= len(str(out[k].get('name', ''))):
                    continue
            except Exception:
                pass
        out[k] = {
            'name': nm,
            'eco': str(eco[i] or '').strip(),
            'eco_volume': str(vol[i] or '').strip(),
        }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"Wrote {len(out)} entries -> {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
