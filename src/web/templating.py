import os
from pathlib import Path

from starlette.templating import Jinja2Templates

# TODO setup routes properly
def hardcoded_url_for(name: str, **kwargs):
    if name == "static" and not os.environ.get("LOCAL") == "true":
        # kwargs should contain 'path'
        path = kwargs.get("path", "")
        return f"https://benbanerjeerichards.com/finance/static{path}"
    # fallback for other endpoints if needed
    return f"/{name}/{kwargs.get("path", "")}"


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals['hardcoded_url_for'] = hardcoded_url_for
