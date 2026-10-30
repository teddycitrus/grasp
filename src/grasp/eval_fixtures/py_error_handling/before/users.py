import json


def load_user(store, user_id):
    raw = store[user_id]
    user = json.loads(raw)
    user["id"] = user_id
    return user


def save_user(store, user):
    store[user["id"]] = json.dumps(user)
