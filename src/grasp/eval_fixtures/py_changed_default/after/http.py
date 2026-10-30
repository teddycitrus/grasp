import time
import urllib.request


def fetch(url, timeout=30, retries=3, max_delay=8):
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except OSError:
            attempt += 1
            if attempt > retries:
                raise
            time.sleep(min(2 ** attempt, max_delay))
