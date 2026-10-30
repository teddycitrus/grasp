def search(items, query):
    query = query.lower()
    matches = []
    for item in items:
        if query in item.name.lower():
            matches.append(item)
    return matches
