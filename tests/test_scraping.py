"""Same-host scraping, bounded redirects, robots and incremental corpus imports."""
import copy
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import load_config
import scraping
import import_dataset
from rag import index

TEXT = 'Reviewed documentation about validating inputs, testing limits and handling errors safely. '


def html(links=()):
    return ('<html><p>' + TEXT + '</p>' + ''.join(f'<a href="{link}">Read more</a>' for link in links) + '</html>').encode()


class ScrapingTests(unittest.TestCase):
    def setUp(self):
        self.settings = copy.deepcopy(load_config('scraping'))
        self.settings['policy']['max_pages'] = 50
        self.settings['policy']['max_depth'] = 2
        self.settings['runtime']['delay_seconds'] = 0
        self.fetches = []
        self.pages = {'https://example.org/': html(['/guide', '/guide#section', '/',
                       'https://other.org/out', 'https://sub.example.org/out', '/search?q=1', '/logo.png']),
                      'https://example.org/guide': html(['/deep']),
                      'https://example.org/deep': html(['/too-deep'])}
        self.robots = b'User-agent: *\nAllow: /\n'
        self.addCleanup(patch.stopall)
        patch.object(scraping, 'load_config', return_value=self.settings).start()
        patch.object(scraping, 'fetch_url', side_effect=self.fetch).start()
        self.sleep = patch.object(scraping.time, 'sleep').start()

    def fetch(self, url, **kwargs):
        if 'allow_url' in kwargs:
            kwargs['allow_url'](url)
        self.fetches.append(url)
        if url.endswith('/robots.txt'):
            return 'robots.txt', self.robots, url
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return 'page.html', page, url

    def crawl(self, **kwargs):
        return scraping.crawl('https://example.org/', used_sources=kwargs.get('used_sources', set()),
                              seen_urls=kwargs.get('seen_urls', set()), cache=kwargs.get('cache', {}))

    def test_bfs_stays_on_exact_hostname_and_bounds_depth(self):
        with redirect_stdout(io.StringIO()):
            sources, errors = self.crawl()
        self.assertEqual(errors, [])
        self.assertEqual([item[2] for item in sources], list(self.pages))
        self.assertEqual(len(set(self.fetches)), len(self.fetches))
        self.assertNotIn('https://example.org/too-deep', self.fetches)

    def test_page_budget_bounds_frontier(self):
        self.settings['policy']['max_pages'] = 2
        with redirect_stdout(io.StringIO()):
            sources, errors = self.crawl()
        self.assertEqual(len(sources), 2)
        self.assertEqual(errors, [])
        self.assertNotIn('https://example.org/deep', self.fetches)

    def test_seed_directory_restricts_new_and_cached_links(self):
        seed = 'https://example.org/book/man.html'
        links = ['/book/chapter.html', '/book/sub/guide.html', '/outside.html',
                 '/book-other/page.html', '/book/../outside.html',
                 '/book/%2e%2e/outside.html', '/book/%252e%252e/outside.html',
                 'http://example.org/book/page.html', 'https://example.org:80/book/page.html']
        self.pages = {seed: html(links),
                      'https://example.org/book/chapter.html': html(),
                      'https://example.org/book/sub/guide.html': html()}
        with redirect_stdout(io.StringIO()):
            sources, errors = scraping.crawl(seed, used_sources=set(), seen_urls=set(), cache={})
        self.assertEqual(errors, [])
        self.assertEqual([item[2] for item in sources], list(self.pages))
        self.fetches.clear()
        with redirect_stdout(io.StringIO()):
            sources, errors = scraping.crawl(seed, used_sources={f'url:{seed}'}, seen_urls=set(),
                                            cache={seed: {'links': ['https://example.org' + link for link in links[:7]]}})
        self.assertEqual(errors, [])
        self.assertEqual(len(sources), 2)
        self.assertFalse(any('/outside' in url or '/book-other' in url for url in self.fetches))

    def test_directory_seed_with_trailing_slash_keeps_that_directory(self):
        scope = scraping.SeedScope('https://example.org/book/', False)
        self.assertEqual(scope.check('https://example.org/book/chapter.html'), 'https://example.org/book/chapter.html')
        with self.assertRaises(ValueError):
            scope.check('https://example.org/other/chapter.html')

    def test_robots_disallow_and_delay_are_enforced(self):
        self.robots = b'User-agent: *\nDisallow: /guide\nCrawl-delay: 2\n'
        with redirect_stdout(io.StringIO()):
            sources, errors = self.crawl()
        self.assertEqual(len(sources), 1)
        self.assertIn('robots.txt', errors[0]['reason'])
        self.assertNotIn('https://example.org/guide', self.fetches)
        self.sleep.assert_called()

    def test_robots_failure_is_reported_without_fetching_pages(self):
        with patch.object(scraping, 'fetch_url', side_effect=OSError('network unavailable')), redirect_stdout(io.StringIO()):
            sources, errors = self.crawl()
        self.assertEqual(sources, [])
        self.assertIn('network unavailable', errors[0]['reason'])

    def test_failed_page_does_not_block_other_pages(self):
        self.pages['https://example.org/'] = html(['/bad', '/guide'])
        self.pages['https://example.org/bad'] = OSError('unavailable')
        with redirect_stdout(io.StringIO()):
            sources, errors = self.crawl()
        self.assertEqual(len(errors), 1)
        self.assertIn('https://example.org/guide', [source[2] for source in sources])

    def test_incremental_cache_retry_and_reset(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            root = Path(directory) / 'corpus'
            root.mkdir()
            out = root / 'imported'
            (root / 'scraping').write_text('# Sources\n\nhttps://example.org/\nhttps://example.org/\n')
            first, errors = import_dataset.import_directory(root, out)
            self.assertEqual(len(first), 3)
            self.assertEqual(errors, [])
            self.assertTrue((out / 'scraping_state.json').exists())
            self.fetches.clear()
            second, errors = import_dataset.import_directory(root, out)
            self.assertEqual(second, [])
            self.assertEqual(errors, [])
            self.assertEqual(self.fetches, [])
            import_dataset.import_directory(root, out, force=True)
            self.assertIn('https://example.org/guide', self.fetches)
            self.assertNotIn('scraping', [item['member'] for item in first])
            database = Path(directory) / 'index.sqlite3'
            index(root, database)
            import sqlite3
            with sqlite3.connect(database) as db:
                sources = [row[0] for row in db.execute('SELECT source FROM documents')]
            self.assertFalse(any('scraping_state' in name for name in sources))

    def test_persisted_links_are_revalidated_and_failed_pages_retry(self):
        cache = {'https://example.org/': {'links': ['https://other.org/', 'http://127.0.0.1/', 'https://example.org/guide']}}
        with redirect_stdout(io.StringIO()):
            sources, errors = self.crawl(cache=cache, used_sources={'url:https://example.org/'})
        self.assertEqual(errors, [])
        self.assertEqual(len(sources), 2)
        self.assertNotIn('https://other.org/', self.fetches)
        # No successful import state for /guide: fetch it again even with cached links.
        self.fetches.clear()
        with redirect_stdout(io.StringIO()):
            self.crawl(cache=cache, used_sources={'url:https://example.org/'})
        self.assertIn('https://example.org/guide', self.fetches)

    def test_symlink_and_generated_source_lists_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'corpus'
            root.mkdir()
            external = Path(directory) / 'outside'
            external.write_text('https://example.org/')
            (root / 'scraping').symlink_to(external)
            (root / 'imported').mkdir()
            (root / 'imported/scraping').write_text('https://example.org/')
            with redirect_stdout(io.StringIO()):
                sources, _ = import_dataset.collect_directory(root)
            self.assertEqual(sources, [])
            self.assertEqual(self.fetches, [])


class BoundedFetchTests(unittest.TestCase):
    def test_redirect_outside_directory_is_rejected_before_request(self):
        opener = unittest.mock.Mock()
        headers = Message()
        headers['Location'] = '/outside.html'
        seed = 'https://example.org/book/man.html'
        opener.open.side_effect = urllib.error.HTTPError(seed, 302, 'redirect', headers, None)
        scope = scraping.SeedScope(seed, False)
        with patch.object(import_dataset.urllib.request, 'build_opener', return_value=opener), patch.object(import_dataset, 'validate_url'):
            with self.assertRaisesRegex(ValueError, 'outside'):
                import_dataset.fetch_url(seed, allowed_host='example.org', allow_url=scope.check)
        opener.open.assert_called_once()

    def test_external_redirect_is_rejected_before_request(self):
        opener = unittest.mock.Mock()
        headers = Message()
        headers['Location'] = 'https://other.org/out'
        opener.open.side_effect = urllib.error.HTTPError('https://example.org/', 302, 'redirect', headers, None)
        with patch.object(import_dataset.urllib.request, 'build_opener', return_value=opener), patch.object(import_dataset, 'validate_url') as validate:
            with self.assertRaisesRegex(import_dataset.ExtractError, 'outside'):
                import_dataset.fetch_url('https://example.org/', allowed_host='example.org', max_bytes=100)
        opener.open.assert_called_once()
        validate.assert_called_once_with('https://example.org/')

    def test_response_byte_limit_is_enforced(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'x' * 11
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(import_dataset.urllib.request, 'build_opener', return_value=opener), patch.object(import_dataset, 'validate_url'):
            with self.assertRaisesRegex(import_dataset.ExtractError, 'byte limit'):
                import_dataset.fetch_url('https://example.org/', allowed_host='example.org', max_bytes=10)
        response.read.assert_called_once_with(11)
