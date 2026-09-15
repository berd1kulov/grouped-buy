"""Small local maintenance CLI; creates a backup before resetting demo data."""
import argparse
import shutil
from pathlib import Path
from app import config
from app.store import init, connect, now


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['init','reset-demo','status'])
    args=parser.parse_args()
    if args.command=='reset-demo':
        if config.MODE!='demo':raise SystemExit('Reset is available only in demo mode.')
        path=Path(config.DB_PATH)
        if path.exists():
            import sqlite3
            target=path.with_name(path.stem+'-backup-'+str(now())+'.sqlite3')
            with connect(True) as db:
                mode=db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()
                if not mode or mode['value']!='demo':raise SystemExit('This is not a demo database.')
            with sqlite3.connect(str(path)) as src, sqlite3.connect(str(target)) as dest:src.backup(dest)
            path.unlink()
            for suffix in ('-wal','-shm'):
                extra=Path(str(path)+suffix)
                if extra.exists():extra.unlink()
            print('Demo backup saved:',target)
        init();print('Demo restored. Reload the browser.')
    elif args.command=='init':
        init();print('Database initialized:',config.DB_PATH)
    else:
        init()
        with connect() as db:
            for table in ('users','pools','orders','payments','refunds','cases'):
                print(table,db.execute('SELECT count(*) FROM '+table).fetchone()[0])
            print('Notifications needing attention:',db.execute('SELECT count(*) FROM notifications WHERE sent_at IS NULL AND attempts>=5').fetchone()[0])

if __name__=='__main__':main()
