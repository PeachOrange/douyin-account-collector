import argparse
import csv
import json
import os
import sqlite3


def export_csv(db_path, out_csv):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        "select aweme_id,title,author_name,sec_uid,href,output,size,status,reason,download_url_source,checked_at from downloads order by checked_at desc"
    ).fetchall()
    headers = [d[0] for d in cur.description]
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    conn.close()
    return len(rows)


def export_xlsx_fallback(out_xlsx, rows_csv_path):
    # Safe fallback: create a JSON sidecar note when openpyxl isn't installed.
    note = {
        'status': 'xlsx_not_generated',
        'reason': 'openpyxl_not_installed',
        'csv_source': rows_csv_path,
        'hint': 'Install openpyxl in the runtime environment to emit a real XLSX file.'
    }
    with open(out_xlsx + '.json', 'w', encoding='utf-8') as f:
        json.dump(note, f, ensure_ascii=False, indent=2)


def export_xlsx_if_possible(db_path, out_xlsx):
    try:
        from openpyxl import Workbook
    except Exception:
        return False
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        "select aweme_id,title,author_name,sec_uid,href,output,size,status,reason,download_url_source,checked_at from downloads order by checked_at desc"
    ).fetchall()
    headers = [d[0] for d in cur.description]
    wb = Workbook()
    ws = wb.active
    ws.title = 'downloads'
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    os.makedirs(os.path.dirname(out_xlsx), exist_ok=True)
    wb.save(out_xlsx)
    conn.close()
    return True


def main():
    parser = argparse.ArgumentParser(description='Export Douyin collector results from SQLite into CSV/XLSX')
    parser.add_argument('--db', required=True)
    parser.add_argument('--csv', required=True)
    parser.add_argument('--xlsx')
    args = parser.parse_args()

    count = export_csv(args.db, args.csv)
    result = {'rows': count, 'csv': args.csv}
    if args.xlsx:
        ok = export_xlsx_if_possible(args.db, args.xlsx)
        result['xlsx'] = args.xlsx
        result['xlsxGenerated'] = ok
        if not ok:
            export_xlsx_fallback(args.xlsx, args.csv)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
