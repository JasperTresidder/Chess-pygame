import json
import os
import sys


def _pick_better_name(existing: str, candidate: str) -> bool:
    """Return True if candidate should replace existing."""
    try:
        if not existing:
            return True
        if not candidate:
            return False
        # Prefer the shortest non-empty name.
        return len(candidate) < len(existing)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    full = False
    if '--full' in argv:
        full = True
        argv.remove('--full')

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parquet_path = os.path.join(root, 'lit', 'database', 'chess-openings', 'data', 'train-00000-of-00001.parquet')
    out_path = os.path.join(root, 'lit', 'database', 'chess-openings', 'openings_epd_full.json' if full else 'openings_epd.json')

    if not os.path.exists(parquet_path):
        print(f"Missing parquet: {parquet_path}")
        return 2

    try:
        import pyarrow.parquet as pq
    except Exception as e:
        print("pyarrow is required to build the openings index")
        print(e)
        return 3

    cols = ['epd', 'name', 'eco', 'eco-volume'] + (['uci', 'pgn'] if full else [])
    table = pq.read_table(parquet_path, columns=cols)

    epd = table.column('epd').to_pylist()
    name = table.column('name').to_pylist()
    eco = table.column('eco').to_pylist()
    vol = table.column('eco-volume').to_pylist()
    uci = table.column('uci').to_pylist() if full else None
    pgn = table.column('pgn').to_pylist() if full else None

    out: dict[str, dict] = {}
    for i in range(len(epd)):
        k = str(epd[i] or '').strip()
        if not k:
            continue
        nm = str(name[i] or '').strip()
        if not nm:
            continue

        if k in out:
            try:
                if not _pick_better_name(str(out[k].get('name', '') or ''), nm):
                    continue
            except Exception:
                pass

        rec: dict[str, str] = {
            'name': nm,
            'eco': str(eco[i] or '').strip(),
            'eco_volume': str(vol[i] or '').strip(),
        }
        if full:
            try:
                rec['uci'] = str((uci[i] if uci is not None else '') or '').strip()
            except Exception:
                rec['uci'] = ''
            try:
                rec['pgn'] = str((pgn[i] if pgn is not None else '') or '').strip()
            except Exception:
                rec['pgn'] = ''

        out[k] = rec

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)

    mode = 'FULL' if full else 'MIN'
    print(f"Wrote {len(out)} {mode} entries -> {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
