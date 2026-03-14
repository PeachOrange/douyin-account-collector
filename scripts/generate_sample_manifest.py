import argparse
import json
import os
import re
import sqlite3
from datetime import datetime


def clean_title(s: str) -> str:
    s = (s or '').replace('\r', ' ').strip()
    s = re.sub(r'\n+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def load_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select aweme_id,title,author_name,sec_uid,href,output,size,status,reason,download_url_source,checked_at from downloads where status='ok' order by checked_at desc"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def to_markdown(rows, profile_url):
    total_size = sum(int(r.get('size') or 0) for r in rows)
    lines = []
    lines.append('# Douyin Sample Manifest')
    lines.append('')
    lines.append(f'- Generated: {datetime.now().astimezone().isoformat(timespec="seconds")}')
    lines.append(f'- Profile: {profile_url}')
    lines.append(f'- Sample count: {len(rows)}')
    lines.append(f'- Total size: {total_size} bytes')
    lines.append('')
    lines.append('## Samples')
    lines.append('')
    for i, r in enumerate(rows, 1):
        title = clean_title(r.get('title'))
        lines.append(f'{i}. **{title}**')
        lines.append(f'   - aweme_id: `{r.get("aweme_id")}`')
        lines.append(f'   - href: `{r.get("href")}`')
        lines.append(f'   - file: `{r.get("output")}`')
        lines.append(f'   - size: `{r.get("size")}`')
        lines.append(f'   - source: `{r.get("download_url_source")}`')
        lines.append(f'   - checked_at: `{r.get("checked_at")}`')
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Generate a human-readable sample manifest from the Douyin collector SQLite database')
    parser.add_argument('--db', required=True)
    parser.add_argument('--profile-url', required=True)
    parser.add_argument('--json-out', required=True)
    parser.add_argument('--md-out', required=True)
    args = parser.parse_args()

    rows = load_rows(args.db)
    payload = {
        'generatedAt': datetime.now().astimezone().isoformat(timespec='seconds'),
        'profile': args.profile_url,
        'count': len(rows),
        'items': rows,
    }
    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    os.makedirs(os.path.dirname(args.md_out), exist_ok=True)
    with open(args.json_out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(args.md_out, 'w', encoding='utf-8') as f:
        f.write(to_markdown(rows, args.profile_url))
    print(json.dumps({'count': len(rows), 'json': args.json_out, 'markdown': args.md_out}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
