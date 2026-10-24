#!/usr/bin/env python3
"""Root-managed fixed worker. Runs outside Site Control service cgroup."""
import os,sys,re,subprocess
from pathlib import Path

def run(root):
    # Only secure 16-hex update ids, never shell input.
    root=Path(root)
    for ready in sorted(root.glob('*.ready'))[:15]:
        rid=ready.stem
        if not re.fullmatch(r'[a-f0-9]{16}',rid):
            ready.unlink(missing_ok=True);continue
        ready.unlink(missing_ok=True)  # Clear first to avoid retriggering on failure.
        cmd=[sys.executable,'-m','sitecontrol.self_update','apply',rid]
        try: subprocess.run(cmd,check=False,timeout=105,cwd=os.environ.get('SITE_CONTROL_PROJECT_ROOT','/var/www/SVG'))
        except (OSError,subprocess.TimeoutExpired) as exc:
            print('Update execution failed:',rid,type(exc).__name__,file=sys.stderr)
if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit(2)
    run(sys.argv[1])
