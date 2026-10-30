class Settings:
    def __init__(self, loader, cache):
        self.loader = loader
        self.cache = cache
        self.values = {}
        self.version = 0

    def reload(self):
        new_values = self.loader.load()
        if new_values == self.values:
            return self.values
        self.cache = {}
        self.values = new_values
        self.version = self.values.get("version", 0)
        return self.values

    def get(self, key, default=None):
        if key not in self.cache:
            self.cache[key] = self.values.get(key, default)
        return self.cache[key]
