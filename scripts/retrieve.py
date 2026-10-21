#!/usr/bin/env python3
"""Read-only candidate retrieval with original PDF page coordinates."""
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from common import check_sources, data_directory, normalize, open_index


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def query_tokens(terms):
    tokens = set()
    for term in terms:
        for run in re.findall(r'[\u3400-\u9fff]+|[A-Za-z0-9]+', term):
            if '\u3400' <= run[0] <= '\u9fff':
                tokens.update(run[i:i + 2] for i in range(len(run) - 1)) if len(run) > 1 else tokens.add(run)
            else:
                tokens.add(run.lower())
    return tokens


def main():
    parser = argparse.ArgumentParser(description='本地《毛泽东文集》候选引文检索；PDF页序从1起算，需另行核对原页。')
    parser.add_argument('terms', nargs='*', help='2—6个关键词或短语；主题拆词由代理完成')
    parser.add_argument('--data-dir', help='与建立索引时相同的本地数据目录')
    parser.add_argument('--volume', type=int, choices=range(1, 9))
    parser.add_argument('--limit', type=int, default=8)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--page', help='卷:PDF页序，例如7:30')
    actions.add_argument('--catalogue', action='store_true', help='列出本地PDF书签导航')
    actions.add_argument('--status', action='store_true', help='查看索引、实际覆盖卷次和源文件状态')
    parser.add_argument('--context', type=int, default=0, help='--page前后各读取几页（0—5）')
    parser.add_argument('--render', help='与--page一起使用，输出该页PNG路径')
    args = parser.parse_args()
    if not 1 <= args.limit <= 50 or not 0 <= args.context <= 5:
        parser.error('--limit必须为1—50，--context必须为0—5。')
    if (args.render or args.context) and not args.page:
        parser.error('--render和--context必须与--page一起使用。')
    if args.terms and (args.page or args.catalogue or args.status):
        parser.error('关键词检索不能与--page、--catalogue或--status合用。')
    if not args.terms and not (args.page or args.catalogue or args.status):
        parser.error('请给出关键词、--page、--catalogue或--status。')
    directory = data_directory(args.data_dir)
    if args.status and not (directory / 'corpus.sqlite').exists():
        emit({'ready': False, 'data_dir': str(directory), 'next_step': '先运行rebuild_index.py指定PDF目录建立索引。'})
        return
    db = None
    try:
        db = open_index(directory)
        if args.status:
            volumes = []
            for row in db.execute('SELECT * FROM volumes ORDER BY volume'):
                item = dict(row)
                item['low_text_pdf_pages'] = json.loads(item['low_text_pdf_pages'])
                volumes.append(item)
            errors = check_sources(db)
            emit({'ready': not errors, 'data_dir': str(directory), 'volumes': volumes,
                  'indexed_pages': db.execute('SELECT count(*) FROM pages').fetchone()[0],
                  'missing_volumes': sorted(set(range(1, 9)) - {x['volume'] for x in volumes}),
                  'source_errors': errors,
                  'note': '覆盖范围以本状态为准；ready仅指索引和源路径可用，不代表逐页校勘完成。'})
            return
        volume = args.volume
        page = None
        if args.page:
            if not re.fullmatch(r'[1-8]:[1-9]\d*', args.page):
                raise ValueError('--page格式应为卷:PDF页序，例如7:30。')
            volume, page = map(int, args.page.split(':'))
            if args.volume and volume != args.volume:
                raise ValueError('--volume与--page中的卷号不一致。')
        if volume and not db.execute('SELECT 1 FROM volumes WHERE volume=?', (volume,)).fetchone():
            raise ValueError('当前索引没有卷{}，请用--status查看覆盖范围。'.format(volume))
        errors = check_sources(db, volume)
        if errors:
            raise ValueError(' '.join(errors))
        if args.catalogue:
            sql = 'SELECT volume,pdf_page,level,title FROM catalogue'
            params = ()
            if volume:
                sql += ' WHERE volume=?'
                params = (volume,)
            for row in db.execute(sql + ' ORDER BY volume,pdf_page,rowid', params):
                emit(dict(row))
            return
        if page:
            source = db.execute('SELECT * FROM volumes WHERE volume=?', (volume,)).fetchone()
            if not 1 <= page <= source['pdf_pages']:
                raise ValueError('PDF页序超出本卷范围1—{}。'.format(source['pdf_pages']))
            for row in db.execute('SELECT * FROM pages WHERE volume=? AND pdf_page BETWEEN ? AND ? ORDER BY pdf_page',
                                  (volume, max(1, page - args.context), min(source['pdf_pages'], page + args.context))):
                result = dict(row)
                result['source_pdf'] = source['path']
                result['warning'] = '文字层整页候选；可能混有页眉、脚注和水印，尚未核对原页。'
                emit(result)
            if args.render:
                try:
                    import fitz
                except ImportError:
                    raise ValueError('渲染需要PyMuPDF，请安装requirements.txt中的依赖。')
                dest = Path(args.render).expanduser().resolve()
                if dest.suffix.lower() != '.png':
                    raise ValueError('--render输出文件扩展名须为.png。')
                if dest.exists():
                    raise ValueError('输出图片已存在，请选择新的文件名。')
                # Recheck the fingerprint before using a page image as evidence.
                sha = hashlib.sha256()
                with Path(source['path']).open('rb') as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        sha.update(chunk)
                if sha.hexdigest() != source['sha256']:
                    raise ValueError('原PDF内容与索引指纹不一致，请重建索引。')
                dest.parent.mkdir(parents=True, exist_ok=True)
                with fitz.open(source['path']) as doc:
                    doc[page - 1].get_pixmap(matrix=fitz.Matrix(1.6, 1.6)).save(str(dest))
                emit({'rendered': str(dest), 'volume': volume, 'pdf_page': page})
            return
        terms = [normalize(t) for t in args.terms if normalize(t)]
        tokens = query_tokens(terms)
        if not tokens:
            raise ValueError('检索词不含可索引字符。')
        match = ' OR '.join('"' + t + '"' for t in sorted(tokens))
        sql = '''SELECT p.*,v.path,bm25(search) AS rank FROM search
                 JOIN pages p ON p.id=search.rowid JOIN volumes v ON p.volume=v.volume
                 WHERE search MATCH ?'''
        params = [match]
        if volume:
            sql += ' AND p.volume=?'
            params.append(volume)
        rows = db.execute(sql + ' ORDER BY rank LIMIT 400', params).fetchall()
        rows = sorted(rows, key=lambda r: (-sum(t.lower() in r['text'].lower() for t in terms), r['rank']))
        for row in rows[:args.limit]:
            text = row['text']
            hits = [t for t in terms if t.lower() in text.lower()]
            at = min((text.lower().index(t.lower()) for t in hits), default=0)
            emit({'volume': row['volume'], 'pdf_page': row['pdf_page'],
                  'bookmark_title': row['bookmark_title'], 'matched_terms': hits,
                  'candidate_excerpt': text[max(0, at - 100):min(len(text), at + 260)],
                  'source_pdf': row['path'],
                  'warning': '检索候选；书签不是已核实篇名，PDF页序不是印刷页码。引文须回读原页。'})
        if not rows:
            emit({'results': [], 'note': '未找到候选，请换用历史用语、同义词或更短关键词；不能据此断言文集没有相关论述。'})
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as e:
        parser.exit(1, '检索失败：{}\n'.format(e))
    finally:
        if db is not None:
            db.close()


if __name__ == '__main__':
    main()
