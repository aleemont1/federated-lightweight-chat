"""
Orchestrator API: landing page, login (starts the user's node) and stop (removes it).
Endpoints are sync on purpose: Docker SDK calls block, so FastAPI runs them in its threadpool.
"""

import contextlib
import logging
from functools import cache
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config.settings import settings
from src.services.accounts import Accounts
from src.services.orchestrator import MaxUsersReachedError, OrchestratorService, normalize_username

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FLC Orchestrator")
app.mount("/static", StaticFiles(directory="src/static"), name="static")
templates = Jinja2Templates(directory="src/templates")


@cache
def get_orchestrator() -> OrchestratorService:
    """Created on first request, so importing this module doesn't need a Docker daemon."""
    return OrchestratorService(max_users=settings.max_users)


@cache
def get_accounts() -> Accounts:
    """The account store, opened on first request."""
    return Accounts(settings.accounts_db)


def _landing(
    request: Request, orch: OrchestratorService, error: Optional[str] = None, code: int = 200
) -> HTMLResponse:
    context = {"active_users": orch.get_active_users(), "domain": settings.public_domain, "error": error}
    return templates.TemplateResponse(request, "landing.html", context, status_code=code)


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, orch: OrchestratorService = Depends(get_orchestrator)) -> HTMLResponse:
    """Landing page: lists active users and allows login/logout."""
    return _landing(request, orch)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    orch: OrchestratorService = Depends(get_orchestrator),
    accounts: Accounts = Depends(get_accounts),
) -> Response:
    """
    Checks the password (the first login claims the username), starts the user's node and
    answers with the loading page, which carries the node's login link. It's the POST response
    rather than a redirect target, so nobody can fetch someone else's link with a plain GET.
    """
    try:
        name = normalize_username(username)
        if not accounts.login(name, password):
            return _landing(request, orch, "Wrong username or password.", status.HTTP_401_UNAUTHORIZED)
        orch.spawn_user_node(name)
    except ValueError as e:
        return _landing(request, orch, str(e), status.HTTP_400_BAD_REQUEST)
    except MaxUsersReachedError as e:
        return _landing(request, orch, str(e), status.HTTP_503_SERVICE_UNAVAILABLE)
    # ponytail: the token travels in the URL once (history, proxy logs); the node swaps it for a cookie.
    node_url = f"http://{name}.{settings.public_domain}/api/auth?token={orch.node_token(name)}"
    return templates.TemplateResponse(request, "loading.html", {"user": name, "node_url": node_url})


@app.get("/status/{user}")
def node_status(user: str, orch: OrchestratorService = Depends(get_orchestrator)) -> Dict[str, bool]:
    """Polled by the loading page."""
    with contextlib.suppress(ValueError):
        return {"ready": orch.is_ready(normalize_username(user))}
    return {"ready": False}


@app.post("/stop")
def stop(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    orch: OrchestratorService = Depends(get_orchestrator),
    accounts: Accounts = Depends(get_accounts),
) -> Response:
    """Stops and removes the user's node, ending all its sessions. The data volume is kept."""
    try:
        name = normalize_username(username)
    except ValueError:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)  # can't have a node
    if not accounts.verify(name, password):
        return _landing(request, orch, "Wrong username or password.", status.HTTP_401_UNAUTHORIZED)
    orch.stop_user_node(name)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
