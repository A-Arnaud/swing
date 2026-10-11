"""GitHub login through the local credential manager; no token is sent to the UI."""
import json
import os
import subprocess
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def credential():
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    result = subprocess.run(['git', 'credential', 'fill'],
                            input='protocol=https\nhost=github.com\n\n', text=True,
                            capture_output=True, env=env, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or not fields.get('password'):
        raise ValueError('github_not_connected')
    return fields['password']


def request(path):
    token = credential()
    req = Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'User-Agent': 'SWING-local', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urlopen(req, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError('github_access_denied' if error.code in (401, 403) else 'github_unavailable') from None


class GitHubAccount:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.cached = None
        self.checked = 0
        self.process_completed = False

    def status(self):
        with self.lock:
            connecting = bool(self.process and self.process.poll() is None)
            if self.process and not connecting and not self.process_completed:
                self.cached = None
                self.checked = 0
                self.process_completed = True
            if self.cached is None or time.monotonic() - self.checked > 15:
                try:
                    user = request('/user')
                    self.cached = {'connected': True, 'login': user['login']}
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.cached = {'connected': False, 'login': None}
                self.checked = time.monotonic()
            result = dict(self.cached, connecting=connecting)
            if self.process and self.process.poll() is not None and self.process.returncode:
                result['error'] = 'github_connection_failed'
            return result

    def connect(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return {'status': 'connecting'}
            # The operator clicked Connect: GCM performs the browser OAuth flow
            # and stores credentials in the OS credential store, outside SWING.
            self.process = subprocess.Popen(
                ['git', 'credential-manager', 'github', 'login', '--browser', '--force'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.cached = None
            self.checked = 0
            self.process_completed = False
            return {'status': 'connecting'}


def repositories(page=1):
    if type(page) is not int or not 1 <= page <= 1000:
        raise ValueError('invalid_repository_page')
    query = urlencode({'affiliation': 'owner,collaborator,organization_member',
                       'per_page': 100, 'sort': 'updated', 'page': page})
    data = request('/user/repos?' + query)
    items = [{'name': repo['full_name'], 'url': repo['html_url'],
              'private': repo['private'], 'language': repo.get('language'),
              'archived': repo.get('archived', False)} for repo in data
             if repo.get('permissions', {}).get('pull', True)]
    return {'repositories': items, 'next_page': page + 1 if len(data) == 100 else None}
