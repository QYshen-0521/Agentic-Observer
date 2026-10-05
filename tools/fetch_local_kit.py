"""Download the official public local cards and runner from a pinned source commit."""
import concurrent.futures
import json
from pathlib import Path
import urllib.request

COMMIT = '33f9a502776020db64ad12aee9b446563ad94bb7'
ROOT = Path(__file__).resolve().parents[1]/'strategy_review_output/v4_rebuild/local-kit'

def main():
    request = urllib.request.Request(
        f'https://api.github.com/repos/gosimfoundation/hackathon-survey26/git/trees/{COMMIT}?recursive=1',
        headers={'User-Agent': 'Agentic-Observer-local-kit'})
    tree = json.load(urllib.request.urlopen(request, timeout=30))['tree']
    paths = [row['path'] for row in tree if row['type'] == 'blob' and row['path'].startswith('examples/_local/')]
    def fetch(path):
        target = ROOT/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(urllib.request.urlopen(
            f'https://raw.githubusercontent.com/gosimfoundation/hackathon-survey26/{COMMIT}/{path}', timeout=45).read())
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, paths))
    print(f'Downloaded {len(paths)} files at {COMMIT}; verify with the bundled verify_engine.py.')

if __name__ == '__main__':
    main()
