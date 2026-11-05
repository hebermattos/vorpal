"""Import a single public web page or plain-text URL as a reviewed corpus candidate.

No crawling, no JavaScript execution, and no authentication. Review imports before training.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import ipaddress
from pathlib import Path
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
TEXT_TYPES = ('text/plain', 'text/html', 'text/markdown', 'text/x-python', 'application/json',
              'application/xml', 'text/xml', 'application/javascript', 'text/javascript')
SKIP = {'script', 'style', 'nav', 'footer', 'header', 'noscript', 'svg', 'form'}

class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.fragments = []
    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.hidden.append(tag)
        if tag in ('p','div','h1','h2','h3','li','pre','br','tr') and not self.hidden:
            self.fragments.append('\n')
    def handle_endtag(self, tag):
        if tag in SKIP and tag in self.hidden:
            self.hidden.remove(tag)
        if tag in ('p','li','pre','tr') and not self.hidden:
            self.fragments.append('\n')
    def handle_data(self, data):
        if not self.hidden:
            self.fragments.append(data)
    def text(self):
        result=''.join(self.fragments)
        return re.sub(r'\n{3,}', '\n\n', result).strip()

def validate_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Forneça uma URL pública HTTP(S), sem credenciais.')
    if p.port not in (None, 80, 443):
        raise ValueError('Portas diferentes de 80 e 443 não são permitidas.')
    host = p.hostname.rstrip('.')
    if host.lower() == 'localhost' or host.lower().endswith('.localhost'):
        raise ValueError('Endereços locais não são permitidos.')
    try:
        addresses = socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80),
                                       type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError('Não foi possível resolver o host.') from exc
    if not addresses:
        raise ValueError('Host sem IP.')
    if not all(ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ValueError('Endereço privado, reservado ou local bloqueado.')
    return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

def fetch(url):
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(MAX_REDIRECTS + 1):
        validate_url(url)
        req=urllib.request.Request(url, headers={'User-Agent': 'VorpalCorpusImporter/1.0',
                                                 'Accept':'text/plain,text/html,text/markdown,application/json'})
        try:
            response=opener.open(req, timeout=15)
        except urllib.error.HTTPError as err:
            if err.code in (301,302,303,307,308):
                dest=err.headers.get('Location')
                if not dest: raise ValueError('Redirecionamento sem Location')
                url=urllib.parse.urljoin(url,dest)
                continue
            raise
        with response:
            if response.status in (301,302,303,307,308):
                dest=response.headers.get('Location')
                if not dest: raise ValueError('Redirecionamento sem Location')
                url=urllib.parse.urljoin(url,dest)
                continue
            media=response.headers.get_content_type().lower()
            if media not in TEXT_TYPES:
                raise ValueError(f'Tipo de conteúdo não suportado: {media}')
            size=response.headers.get('Content-Length')
            if size and int(size)>MAX_BYTES: raise ValueError('Arquivo maior que 2 MB')
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES: raise ValueError('Arquivo maior que 2 MB')
            charset=response.headers.get_content_charset() or 'utf-8'
            try: decoded=raw.decode(charset, errors='replace')
            except LookupError: decoded=raw.decode('utf-8',errors='replace')
            if media=='text/html':
                parser=HTMLText()
                parser.feed(decoded)
                decoded=parser.text()
            decoded=re.sub(r'\n{3,}','\n\n',decoded).strip()
            if len(decoded)<50: raise ValueError('Conteúdo textual insuficiente (menos de 50 caracteres)')
            return url,decoded
    raise ValueError('Excesso de redirecionamentos')

def import_url(url, destination='corpus/imported'):
    final,text=fetch(url)
    folder=Path(destination)
    folder.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(final.encode('utf-8')).hexdigest()[:16]
    path=folder/f'url_{digest}.txt'
    # Keep URL as provenance in a sidecar, outside the training text.
    path.write_text(text+'\n',encoding='utf-8')
    path.with_suffix('.source').write_text(final+'\n',encoding='utf-8')
    return path

def main():
    p=argparse.ArgumentParser(description='Importar URL pública para corpus do Vorpal')
    p.add_argument('url', help='URL de página HTML ou arquivo de texto/código')
    p.add_argument('--out',default='corpus/imported')
    args=p.parse_args()
    try:
        path=import_url(args.url,args.out)
    except (ValueError,urllib.error.URLError,OSError) as exc:
        p.exit(1,f'Erro ao importar: {exc}\n')
    print(f'Salvo: {path}')
    print('Revise a licença, direitos de uso e possíveis segredos ANTES de preparar o dataset.')
    print('Próximo passo: python src/data.py --corpus corpus --out data')

if __name__=='__main__': main()
