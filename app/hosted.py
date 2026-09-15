"""Single-container pilot: proxy, application and polling bot share /data.
One replica only. Hosted execution never enables the local payment simulator.
"""
import os
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse
from app import config
from app.store import init


def validate_environment(env):
    if env.get('APP_MODE') != 'telegram':
        raise ValueError('Hosted deployment requires APP_MODE=telegram; local demo cannot be published.')
    if not env.get('BOT_TOKEN'):
        raise ValueError('Add BOT_TOKEN in hosting Variables.')
    url=env.get('WEBAPP_URL', '').strip()
    if not url and env.get('RAILWAY_PUBLIC_DOMAIN'):
        url='https://'+env['RAILWAY_PUBLIC_DOMAIN']
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Generate a public domain and set WEBAPP_URL to its HTTPS URL.')
    if env.get('DATABASE_PATH')!='/data/birga.sqlite3':
        raise ValueError('Use DATABASE_PATH=/data/birga.sqlite3 and mount a persistent volume at /data.')
    try:
        port=int(env.get('PORT','8000'))
        if not 1<=port<=65535 or port==8081: raise ValueError()
    except ValueError:
        raise ValueError('Public PORT must be a valid port other than internal 8081.')
    return url.rstrip('/')


def main():
    env=os.environ.copy()
    try:
        env['WEBAPP_URL']=validate_environment(env)
        if env.get('RAILWAY_ENVIRONMENT_ID') and env.get('RAILWAY_VOLUME_MOUNT_PATH')!='/data':
            raise ValueError('Attach a Railway Volume mounted at /data before deploying.')
    except ValueError as error:
        raise SystemExit(str(error))
    os.environ['WEBAPP_URL']=env['WEBAPP_URL']
    init()
    processes=[]
    stopped=False
    def stop(signum, frame):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        server_env={**env,'HOST':'127.0.0.1','PORT':'8081'}
        processes.append(subprocess.Popen([sys.executable,'-m','app.server'],env=server_env))
        processes.append(subprocess.Popen([sys.executable,'-m','app.bot'],env=env))
        processes.append(subprocess.Popen(['caddy','run','--config','deploy/Caddyfile','--adapter','caddyfile'],env=env))
        while not stopped:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('A required service exited; restarting the container is required.')
            time.sleep(1)
    finally:
        for process in processes:
            if process.poll() is None:process.terminate()
        for process in processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()

if __name__=='__main__':main()
