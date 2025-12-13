"""
Orchestrator API.
The landing page and login handler for the Federated Chat.
"""

from typing import Any

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.services.orchestrator import MaxUsersReachedError, orchestrator_service

app = FastAPI(title="FLC Orchestrator")

# We reuse the static files for the login css
app.mount("/static", StaticFiles(directory="src/static"), name="static")

# Simple HTML template for the landing page
LANDING_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FLC Federation</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white h-screen flex flex-col items-center justify-center p-4">
    <div class="max-w-md w-full bg-slate-800 p-8 rounded-xl shadow-2xl border border-slate-700">
        <h1 class="text-3xl font-bold text-blue-500 mb-2 text-center">FLC Network</h1>
        <p class="text-slate-400 mb-8 text-center">Federated Lightweight Chat Gateway</p>
        <form action="/login" method="post" class="space-y-6">
            <div>
                <label class="block text-sm font-medium mb-2">Node Identity (Username)</label>
                <input type="text" name="username" required
                    class="w-full bg-slate-900 border border-slate-600
                    rounded px-4 py-3 focus:border-blue-500 outline-none transition"
                    placeholder="alice">
            </div>

            <button type="submit"
                class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3
                rounded-lg transition transform active:scale-95">
                Launch Node & Login
            </button>
        </form>

        <div class="mt-6 border-t border-slate-700 pt-4">
            <h3 class="text-xs font-bold text-slate-500 uppercase mb-2">Active Nodes</h3>
            <ul class="text-sm text-blue-400 space-y-1">
                {% for user in active_users %}
                <li><a href="http://{{ user }}.localhost" class="hover:underline">🟢 {{ user }}</a></li>
                {% else %}
                <li class="text-slate-600 italic">No active nodes</li>
                {% endfor %}
            </ul>
        </div>
    </div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def landing_page() -> Any:
    """Renders the landing page with active nodes list."""
    from jinja2 import Template

    t = Template(LANDING_HTML)
    active = orchestrator_service.get_active_users()
    return t.render(active_users=active)


@app.post("/login")
async def login(username: str = Form(...)) -> RedirectResponse:
    """
    Spawns a container for the user and redirects to their subdomain.
    """
    try:
        orchestrator_service.spawn_user_node(username)

        # Sanitize name for the URL redirection
        safe_name = orchestrator_service._sanitize_username(username)

        # Redirect to the user's specific subdomain
        # Note: In production, this requires wildcard DNS or host file entry
        return RedirectResponse(url=f"http://{safe_name}.localhost", status_code=303)

    except MaxUsersReachedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
