#!/usr/bin/env python3
"""Build local candidate search data from user-supplied PDFs. No network calls."""
import argparse
import bisect
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from common import SCHEMA_VERSION, data_directory, normalize, search_tokens

NUMBERS = {str(n): n for n in range(1, 9)}
NUMBERS.update({c: i for i, c in enumerate('一二三四五六七八', 1)})


def volume_number(name):
    stem = Path(name).stem
    patterns = [r'第\s*([一二三四五六七八1-8])\s*卷', r'卷\s*([一二三四五六七八1-8])(?!\d)', r'(?i)vol(?:ume)?[\s_.-]*([1-8])(?!\d)']
    found = {NUMBERS[n] for pattern in patterns for n in re.findall(pattern, stem)}
    if re.fullmatch(r'[1-8]', stem):
        found.add(int(stem))
    if len(found) != 1:
        raise ValueError('无法唯一识别卷号：{}。请使用 --manifest 明确指定。'.format(name))
    return found.pop()


def resolve_sources(args):
    sources = []
    if args.source_dir:
        directory = Path(args.source_dir).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError('PDF目录不存在。')
        for p in sorted(directory.iterdir()):
            if p.is_file() and p.suffix.lower() == '.pdf':
                sources.append((volume_number(p.name), p))
    else:
        manifest = Path(args.manifest).expanduser().resolve()
        content = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if not isinstance(content, dict) or not isinstance(content.get('volumes'), list):
            raise ValueError('清单格式应为 {"volumes": [{"volume": 1, "path": "文件.pdf"}]}。')
        for item in content['volumes']:
            if not isinstance(item, dict):
                raise ValueError('每条清单记录必须是对象。')
            v, name = item.get('volume'), item.get('path')
            if type(v) is not int or v not in range(1, 9) or not isinstance(name, str) or not name:
                raise ValueError('清单卷号必须为整数1—8，path必须为有效文件路径。')
            p = Path(name).expanduser()
            sources.append((v, (manifest.parent / p).resolve()))
    if not sources:
        raise ValueError('没有找到PDF文件。')
    volumes = [v for v, _ in sources]
    if len(set(volumes)) != len(volumes):
        raise ValueError('出现重复卷号，请去除重复文件或用 --manifest 明确指定每卷。')
    paths = [str(p) for _, p in sources]
    if len(set(paths)) != len(paths):
        raise ValueError('同一个PDF不能重复分配给不同卷号。')
    missing = sorted(set(range(1, 9)) - set(volumes))
    if missing and not args.allow_partial:
        raise ValueError('缺少卷{}；默认要求1—8卷齐全。仅处理已有卷时加 --allow-partial。'.format(','.join(map(str, missing))))
    for _, p in sources:
        if not p.is_file() or p.suffix.lower() != '.pdf':
            raise ValueError('清单源文件不存在或不是PDF：{}'.format(p.name))
    return sorted(sources)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def atomic_text(path, value):
    fd, name = tempfile.mkstemp(prefix='.export-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def build(args):
    try:
        import fitz
    except ImportError:
        raise ValueError('缺少PyMuPDF。请在所用Python环境运行 python3 -m pip install -r requirements.txt。')
    sources = resolve_sources(args)
    directory = data_directory(args.data_dir)
    # Prevent generated personal data from entering the publishable project.
    project = Path(__file__).resolve().parents[1]
    if directory == project or project in directory.parents:
        raise ValueError('数据目录请选在Skill仓库之外，避免提交本地全文和个人路径。')
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.build-', suffix='.sqlite', dir=str(directory))
    os.close(fd)
    db = None
    manifest, catalogue = [], []
    try:
        db = sqlite3.connect(tmp)
        db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE volumes(volume INTEGER PRIMARY KEY, path TEXT, pdf_pages INTEGER,
            characters INTEGER, sha256 TEXT, file_size INTEGER, mtime_ns INTEGER,
            bookmarks INTEGER, low_text_pdf_pages TEXT);
        CREATE TABLE pages(id INTEGER PRIMARY KEY, volume INTEGER, pdf_page INTEGER,
            bookmark_title TEXT, text TEXT, UNIQUE(volume,pdf_page));
        CREATE TABLE catalogue(volume INTEGER, pdf_page INTEGER, level INTEGER, title TEXT);
        CREATE VIRTUAL TABLE search USING fts5(tokens);
        ''')
        db.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', str(SCHEMA_VERSION)))
        db.execute('INSERT INTO metadata VALUES (?,?)', ('created_utc', datetime.now(timezone.utc).isoformat()))
        for v, p in sources:
            before = p.stat()
            sha = digest(p)
            with fitz.open(p) as doc:
                if doc.needs_pass:
                    raise ValueError('卷{}PDF有密码，请先提供可读取的文件。'.format(v))
                if not len(doc):
                    raise ValueError('卷{}PDF没有页面。'.format(v))
                toc = []
                for level, title, pg in doc.get_toc():
                    if 1 <= pg <= len(doc) and title.strip() and not any(x in title for x in ['毛泽东文集', '返回分卷目录']):
                        toc.append((pg, level, title.strip()))
                # Preserve PDF outline order for bookmarks on the same page.
                toc.sort(key=lambda x: x[0])
                starts = [pg for pg, _, _ in toc]
                for pg, level, title in toc:
                    db.execute('INSERT INTO catalogue VALUES(?,?,?,?)', (v, pg, level, title))
                    catalogue.append({'volume': v, 'pdf_page': pg, 'level': level, 'title': title})
                low_text, chars, substantive = [], 0, 0
                for i, page in enumerate(doc, 1):
                    text = normalize(page.get_text())
                    j = bisect.bisect_right(starts, i) - 1
                    title = toc[j][2] if j >= 0 else '前置材料／未定位篇名'
                    cursor = db.execute('INSERT INTO pages(volume,pdf_page,bookmark_title,text) VALUES(?,?,?,?)', (v, i, title, text))
                    db.execute('INSERT INTO search(rowid,tokens) VALUES(?,?)', (cursor.lastrowid, ' '.join(search_tokens(text))))
                    chars += len(text)
                    if len(text) < 30:
                        low_text.append(i)
                    else:
                        substantive += 1
                if not substantive:
                    raise ValueError('卷{}未发现足够文字层，可能是扫描件。请先OCR后重新建立索引。'.format(v))
                after = p.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('卷{}在提取时被修改，旧索引未替换。请重试。'.format(v))
                entry = {'volume': v, 'path': str(p), 'pdf_pages': len(doc), 'characters': chars,
                         'sha256': sha, 'file_size': after.st_size, 'mtime_ns': after.st_mtime_ns,
                         'bookmarks': len(toc), 'low_text_pdf_pages': low_text}
                manifest.append(entry)
                db.execute('INSERT INTO volumes VALUES(?,?,?,?,?,?,?,?,?)',
                           (v, str(p), len(doc), chars, sha, after.st_size, after.st_mtime_ns, len(toc), json.dumps(low_text)))
                print('卷{}：{}页，{}字符，{}条书签，{}页文字量较低'.format(v, len(doc), chars, len(toc), len(low_text)), flush=True)
                if len(low_text) > len(doc) // 5:
                    print('提示：本卷较多页面缺少文字；检索覆盖可能不完整，需原页核对或OCR。', file=sys.stderr)
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('新索引完整性检查失败，保留旧索引。')
        count = db.execute('SELECT count(*) FROM pages').fetchone()[0]
        db.close()
        db = None
        os.replace(tmp, directory / 'corpus.sqlite')
        # SQLite is the authoritative snapshot. Exports are for navigation only.
        atomic_text(directory / 'sources.json', json.dumps({'schema_version': SCHEMA_VERSION, 'volumes': manifest}, ensure_ascii=False, indent=2))
        atomic_text(directory / 'catalogue.json', json.dumps(catalogue, ensure_ascii=False, indent=2))
        atomic_text(directory / 'catalogue.md', '# PDF书签导航（未经逐篇校勘）\n\n' + '\n'.join('- 卷{} · PDF第{}页 · {}'.format(x['volume'], x['pdf_page'], x['title']) for x in catalogue))
        print('建立完成：{}卷，{}页。数据目录：{}'.format(len(manifest), count, directory))
        print('原始PDF未改动；文字层、书签和PDF页序不等于已核实的引文、篇名或印刷页码。')
    finally:
        if db is not None:
            db.close()
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description='为自己的《毛泽东文集》PDF建立本地索引，不下载或上传文献。')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source-dir', help='包含1—8卷独立PDF的目录（不递归）')
    group.add_argument('--manifest', help='卷号与PDF路径映射的JSON清单')
    parser.add_argument('--data-dir', help='仓库之外的本地数据目录')
    parser.add_argument('--allow-partial', action='store_true', help='允许仅索引已有的部分卷，结果不代表全8卷')
    args = parser.parse_args()
    try:
        build(args)
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as e:
        parser.exit(1, '建立失败：{}\n'.format(e))


if __name__ == '__main__':
    main()
