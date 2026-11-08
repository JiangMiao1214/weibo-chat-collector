"""Safe display URL normalization shared by discovery and presentation."""
from urllib.parse import urlsplit


def safe_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None

