def search(items, query, limit=None, case_sensitive=False):
    if not case_sensitive:
        query = query.lower()
    matches = []
    for item in items:
        name = item.name if case_sensitive else item.name.lower()
        if query in name:
            matches.append(item)
            if limit is not None and len(matches) >= limit:
                break
    return matches
