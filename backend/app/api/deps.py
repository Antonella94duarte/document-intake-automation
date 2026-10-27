from app.storage import Storage, get_storage


def storage_dep() -> Storage:
    return get_storage()


def current_actor() -> str:
    """Who is acting. Replaced by the authenticated user once auth is in place."""
    return "anonymous"
