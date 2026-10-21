"""Portable local storage and shared text normalization."""
import os
import re
import sqlite3
import sys
from pathlib import Path

SCHEMA_VERSION = 1


def data_directory(value=None):
    explicit = value or os.environ.get('MAO_WENJI_DATA_DIR')
    if explicit:
        return Path(explicit).expanduser().resolve()
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share')))
    return (base / 'mao-wenji-research').resolve()


def normalize(text):
    # This is for candidate search, never a certified transcription.
    return re.sub(r'\s+', '', text).replace('\x00', '')


def search_tokens(text):
    result = []
    for run in re.findall(r'[\u3400-\u9fff]+|[A-Za-z0-9]+', text):
        if '\u3400' <= run[0] <= '\u9fff':
            result.extend(run)
            result.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            result.append(run.lower())
    return result


def open_index(directory):
    db_path = directory / 'corpus.sqlite'
    if not db_path.is_file():
        raise ValueError('尚未建立索引。先运行 python3 scripts/rebuild_index.py --source-dir "PDF目录"；自定义数据目录时两条命令均加 --data-dir。')
    db = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if not version or int(version[0]) != SCHEMA_VERSION:
            raise ValueError('索引版本不兼容，请重新建立索引。')
    except (sqlite3.Error, ValueError):
        db.close()
        raise ValueError('索引损坏或版本不兼容，请重新建立索引。')
    return db


def check_sources(db, volume=None):
    sql = 'SELECT * FROM volumes'
    rows = db.execute(sql + (' WHERE volume=?' if volume else ''), (volume,) if volume else ()).fetchall()
    errors = []
    for row in rows:
        p = Path(row['path'])
        try:
            st = p.stat()
        except OSError:
            errors.append('卷{}源文件不存在或无法读取，请重新指定源文件建立索引。'.format(row['volume']))
            continue
        if st.st_size != row['file_size'] or st.st_mtime_ns != row['mtime_ns']:
            errors.append('卷{}源文件已变化，请重建索引后再核对引文。'.format(row['volume']))
    return errors
