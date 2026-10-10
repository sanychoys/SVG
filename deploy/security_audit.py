#!/usr/bin/env python3
"""SVGTracker local and optional public web exposure checks (never prints secrets)."""
import argparse
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get('SVGTRACKER_PROJECT_ROOT','/var/www/SVG'))
HOST = os.environ.get('SVGTRACKER_BIND_HOST','127.0.0.1')
PRIVATE_FILES = ('config.py','.env','svgtracker.db','svgtracker.db-wal','svgtracker.db-shm')
PUBLIC_FILES = ('index.html','style.css','script.js','product.css','product.js')
EXPOSED_PROBES = ('/svgtracker.db','/config.py','/.env','/.git/config','/deploy/admin_deploy.py','/venv/pyvenv.cfg')


def audit(root=ROOT,host=HOST):
    problems=[]
    if host not in {'127.0.0.1','::1','localhost'}:
        problems.append('API bind host is not loopback: ' + host)
    for name in PRIVATE_FILES:
        path=root/name
        if path.is_symlink():
            problems.append(name + ': private path is a symlink')
            continue
        if not path.exists():continue
        mode=stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            problems.append(name + ': private permissions are ' + oct(mode))
    for name in PUBLIC_FILES:
        path=root/name
        if path.is_file() and not (stat.S_IMODE(path.stat().st_mode) & 0o004):
            problems.append(name + ': not world-readable by static server')
    return problems


def public_exposure_checks(base_url):
    base=base_url.rstrip('/')
    parsed=urllib.parse.urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path:
        raise ValueError('Use an HTTPS origin, e.g. https://example.org')
    results=[]
    for path in EXPOSED_PROBES:
        request=urllib.request.Request(base+path,method='HEAD',headers={'User-Agent':'SVGTrackerV36-SelfAudit/1.0'})
        try:
            with urllib.request.urlopen(request,timeout=5) as response:
                status=response.status
        except urllib.error.HTTPError as exc:
            status=exc.code
        except Exception as exc:
            results.append((path,'network failure: '+type(exc).__name__,None))
            continue
        results.append((path, status, 200 <= status < 400))
    return results


if __name__ == '__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--root',default=str(ROOT))
    cli.add_argument('--public-url',help='Check common private paths over HTTPS from your own domain')
    args=cli.parse_args()
    problems=audit(Path(args.root))
    if args.public_url:
        for path,status,exposed in public_exposure_checks(args.public_url):
            print(path,'HTTP',status)
            if exposed:problems.append(path + ' is publicly exposed (2xx/3xx)')
    for item in problems:print('WARNING:',item)
    print('LOCAL CHECK RESULT:', 'WARNINGS FOUND' if problems else 'PASS (within local checks)')
    print('Not checked: server firewall, TLS certificate, Nginx live configuration, backup restore, root privilege separation.')
    raise SystemExit(1 if problems else 0)
