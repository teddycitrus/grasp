MAX_UPLOAD_MB = 10
ALLOWED_TYPES = ("image/png", "image/jpeg")


def validate_upload(upload):
    if upload.content_type not in ALLOWED_TYPES:
        return "unsupported type"
    if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
        return "file too large"
    return None
