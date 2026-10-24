# Domain enrollment guide, no registrar API credentials stored.
import re,socket,ipaddress
from html import escape

HOSTNAME=re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$')

def clean_domain(value):
    if not isinstance(value,str):raise ValueError('Введите домен')
    value=value.strip().lower().rstrip('.')
    try:domain=value.encode('idna').decode('ascii')
    except (ValueError,UnicodeError):raise ValueError('Неверный формат домена')
    if not HOSTNAME.fullmatch(domain):raise ValueError('Введите домен вида example.ru')
    return domain

def dns_state(domain,expected_host):
    # Pure DNS lookup is only a hint; registrar DNS must be changed separately.
    try:
        address=str(ipaddress.ip_address(expected_host))
        records=sorted({r[4][0] for r in socket.getaddrinfo(domain,None,proto=socket.IPPROTO_TCP)})
        return {'addresses':records,'points_to_server':address in records,'expected':address}
    except (OSError,ValueError):
        return {'addresses':[],'points_to_server':False,'expected':expected_host}

def nginx_example(domain,folder):
    # This is instruction text only, never auto-installed with root rights.
    return f'''server {{\n    listen 80;\n    server_name {domain};\n    root {folder};\n    index index.html;\n    location / {{ try_files $uri $uri/ =404; }}\n}}'''
