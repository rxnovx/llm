import ssl
from .config import get_settings

def assistant_tls():
    path=get_settings().assistant_ca_bundle
    if not path:return True
    context=ssl.create_default_context()
    context.load_verify_locations(cafile=path)
    return context
