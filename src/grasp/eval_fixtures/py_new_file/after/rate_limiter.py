import time


class RateLimiter:
    def __init__(self, rate, per_seconds=1.0):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.per_seconds = per_seconds
        self.allowance = rate
        self.last = time.monotonic()

    def allow(self):
        now = time.monotonic()
        self.allowance += (now - self.last) * (self.rate / self.per_seconds)
        self.last = now
        if self.allowance > self.rate:
            self.allowance = self.rate
        if self.allowance < 1:
            return False
        self.allowance -= 1
        return True
