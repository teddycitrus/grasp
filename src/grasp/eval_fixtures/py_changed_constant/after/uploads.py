MAX_UPLOAD_MB = 25
ALLOWED_TYPES = ("image/png", "image/jpeg", "image/webp")


def validate_upload(upload):
    if upload.size == 0:
        return "empty file"
    if upload.content_type not in ALLOWED_TYPES:
        return "unsupported type"
    if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
        return "file too large"
    return None
