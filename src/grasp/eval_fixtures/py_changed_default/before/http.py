import time
import urllib.request


def fetch(url, timeout=10, retries=0):
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except OSError:
            attempt += 1
            if attempt > retries:
                raise
            time.sleep(2 ** attempt)
