function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function withRetry<T>(fn: () => Promise<T>, attempts = 5, delayMs = 250, shouldRetry = (err: unknown) => true): Promise<T> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      if (!shouldRetry(err)) {
        throw err;
      }
      lastError = err;
      await sleep(delayMs * 2 ** i);
    }
  }
  throw lastError;
}
