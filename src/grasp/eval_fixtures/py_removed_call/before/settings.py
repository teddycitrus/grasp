class Settings:
    def __init__(self, loader, cache):
        self.loader = loader
        self.cache = cache
        self.values = {}
        self.version = 0

    def reload(self):
        self.cache.clear()
        self.values = self.loader.load()
        self.version = self.values.get("version", 0)
        return self.values

    def get(self, key, default=None):
        if key in self.cache:
            return self.cache[key]
        value = self.values.get(key, default)
        self.cache[key] = value
        return value
