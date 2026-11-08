"""Bounded document crawling within each seed's origin and directory."""
from collections import deque
from html.parser import HTMLParser
import posixpath
import time
import urllib.error
import urllib.parse
import urllib.robotparser

from config import load_config
from import_dataset import ExtractError, SETTINGS, SUPPORTED, classify, fetch_url


def canonical_url(url, *, host=None, follow_queries=False):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 80, 443)):
        raise ValueError('Scraping requires an HTTP(S) URL without credentials on port 80 or 443.')
    hostname = parsed.hostname.lower().rstrip('.').encode('idna').decode('ascii')
    if host is not None and hostname != host:
        raise ValueError('Link is outside the seed hostname.')
    if parsed.query and not follow_queries:
        raise ValueError('Query URLs are disabled by the scraping policy.')
    port = parsed.port
    netloc = hostname if port in (None, 443 if parsed.scheme == 'https' else 80) else f'{hostname}:{port}'
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path or '/', parsed.query, ''))


class Links(HTMLParser):
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.limit = limit

    def handle_starttag(self, tag, attrs):
        if tag == 'a' and len(self.links) < self.limit:
            href = dict(attrs).get('href')
            if href:
                self.links.append(href)


def normalized_path(url):
    path = urllib.parse.urlsplit(url).path or '/'
    for _ in range(4):
        decoded = urllib.parse.unquote(path, errors='strict')
        if decoded == path:
            break
        path = decoded
    else:
        raise ValueError('Excessively encoded scraping path.')
    if '\\' in path or ';' in path or any(ord(char) < 32 for char in path):
        raise ValueError('Ambiguous scraping path.')
    return '/' + posixpath.normpath(path).lstrip('/')


class SeedScope:
    """Keep pages within the seed's origin and containing directory."""
    def __init__(self, seed, follow_queries):
        parsed = urllib.parse.urlsplit(seed)
        self.origin = (parsed.scheme, parsed.netloc)
        self.host = parsed.hostname
        self.follow_queries = follow_queries
        path = normalized_path(seed)
        self.directory = (path.rstrip('/') + '/' if parsed.path.endswith('/')
                          else path.rsplit('/', 1)[0] + '/')

    def check(self, url):
        url = canonical_url(url, host=self.host, follow_queries=self.follow_queries)
        parsed = urllib.parse.urlsplit(url)
        path = normalized_path(url)
        if ((parsed.scheme, parsed.netloc) != self.origin
                or not (path.startswith(self.directory) or path == self.directory.rstrip('/'))):
            raise ValueError('Scraping URL is outside the seed origin or directory.')
        return url


class CrawlPolicy:
    """Check robots for each origin, including redirect destinations, and pace requests."""
    def __init__(self, host, settings, scope=None):
        self.host, self.settings = host, settings
        self.scope = scope
        self.robots = {}
        self.last_request = None

    def wait(self, delay):
        if delay > self.settings['runtime']['max_delay_seconds']:
            raise ExtractError('robots.txt requires a delay above the configured wait limit; skipping this source')
        if self.last_request is not None:
            remaining = delay - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()

    def allow(self, url):
        if self.scope is not None:
            self.scope.check(url)
        parsed = urllib.parse.urlsplit(url)
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        if origin not in self.robots:
            self.wait(self.settings['runtime']['delay_seconds'])
            robot_url = origin + '/robots.txt'
            parser = urllib.robotparser.RobotFileParser(robot_url)
            def allow_robots(target):
                destination = urllib.parse.urlsplit(target)
                if ((destination.scheme, destination.netloc) != (parsed.scheme, parsed.netloc)
                        or normalized_path(target) != '/robots.txt' or destination.query):
                    raise ExtractError('robots.txt redirect outside its metadata endpoint is blocked')
            try:
                _, raw, _ = fetch_url(robot_url, allowed_host=self.host,
                                      max_bytes=self.settings['policy']['max_response_bytes'],
                                      allow_url=allow_robots)
                parser.parse(raw.decode('utf-8-sig').splitlines())
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 410):
                    parser.parse([])
                elif exc.code in (401, 403):
                    parser.parse(['User-agent: *', 'Disallow: /'])
                else:
                    raise
            self.robots[origin] = parser
        parser = self.robots[origin]
        agent = SETTINGS['runtime']['user_agent']
        if not parser.can_fetch(agent, url):
            raise ExtractError('scraping denied by robots.txt')
        delay = max(self.settings['runtime']['delay_seconds'], parser.crawl_delay(agent) or 0)
        rate = parser.request_rate(agent)
        if rate and rate.requests > 0:
            delay = max(delay, rate.seconds / rate.requests)
        self.wait(delay)


def crawl(seed, *, used_sources, seen_urls, cache):
    settings = load_config('scraping')
    policy = settings['policy']
    sources, errors = [], []
    try:
        seed = canonical_url(seed, follow_queries=policy['follow_query_urls'])
        scope = SeedScope(seed, policy['follow_query_urls'])
        scope.check(seed)
    except (ValueError, UnicodeError) as exc:
        return [], [{'url': seed, 'reason': str(exc)}]
    host = urllib.parse.urlsplit(seed).hostname
    access = CrawlPolicy(host, settings, scope)
    queue, scheduled = deque([(seed, 0)]), {seed}
    attempts = 0
    while queue:
        url, depth = queue.popleft()
        attempts += 1
        key = f'url:{url}'
        entry = cache.get(url)
        try:
            cached_links = entry.get('links') if isinstance(entry, dict) else None
            valid_cache = (isinstance(cached_links, list)
                           and all(isinstance(link, str) for link in cached_links))
            if valid_cache and key in used_sources:
                print(f'Skipping previously scraped URL: {url}', flush=True)
                final_url, links = url, cached_links[:policy['max_links_per_page']]
            elif url in seen_urls and valid_cache:
                final_url, links = url, cached_links[:policy['max_links_per_page']]
            else:
                print(f'Scraping URL: {url} (depth {depth})', flush=True)
                name, raw, final_url = fetch_url(url, allowed_host=host,
                                                max_bytes=policy['max_response_bytes'],
                                                allow_url=access.allow)
                final_url = scope.check(final_url)
                links = []
                if classify(name) in ('.html', '.htm'):
                    parser = Links(policy['max_links_per_page'])
                    parser.feed(raw.decode('utf-8-sig'))
                    for href in parser.links:
                        try:
                            link = scope.check(urllib.parse.urljoin(final_url, href))
                        except (ValueError, UnicodeError):
                            continue
                        suffix = classify(urllib.parse.urlsplit(link).path)
                        if not suffix or suffix in SUPPORTED:
                            links.append(link)
                cache[url] = {'links': links}
                if key not in used_sources and url not in seen_urls:
                    sources.append((name, raw, final_url, key))
                seen_urls.add(url)
            if depth < policy['max_depth']:
                for link in links:
                    # Revalidate persisted links; cache files are not trusted input.
                    try:
                        link = scope.check(link)
                    except (ValueError, UnicodeError):
                        continue
                    if link not in scheduled and len(scheduled) < policy['max_pages']:
                        scheduled.add(link)
                        queue.append((link, depth + 1))
        except (ExtractError, ValueError, UnicodeError, urllib.error.URLError, OSError) as exc:
            errors.append({'url': url, 'reason': str(exc)})
    print(f'Scraping complete: {seed}; {attempts} page(s) visited; '
          f'limits: {policy["max_pages"]} pages, depth {policy["max_depth"]}.', flush=True)
    return sources, errors
