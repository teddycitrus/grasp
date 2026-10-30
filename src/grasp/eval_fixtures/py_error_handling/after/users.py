import json


def load_user(store, user_id):
    try:
        raw = store[user_id]
    except KeyError:
        return None
    try:
        user = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"user {user_id} has corrupt data") from exc
    user["id"] = user_id
    return user


def save_user(store, user):
    if "id" not in user:
        raise ValueError("user has no id")
    store[user["id"]] = json.dumps(user)
