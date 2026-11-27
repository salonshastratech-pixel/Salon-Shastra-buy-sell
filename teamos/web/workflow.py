"""Milestone 10 screens: Automations, saved searches, customer progress links, lead-source results, phone app."""
from flask import (Blueprint, Response, abort, current_app, flash, g, make_response, redirect, render_template,
                   request, url_for)

from .. import i18n, messaging
from ..db import get_db
from ..domain import automations as A
from ..domain import reports as R
from ..domain import saved_searches as SS
from ..domain import status_links as SL
from ..domain.errors import PermissionDenied, ValidationError
from ..security import login_required, require

bp = Blueprint("workflow", __name__)
public_bp = Blueprint("public_status", __name__)
pwa_bp = Blueprint("pwa", __name__)


def _err(e: ValidationError) -> str:
    return " ".join(str(v) for v in (e.errors.values() if isinstance(e.errors, dict) else [e.errors]))


# -- Automations -----------------------------------------------------------------------------------------------
@bp.route("/automations", methods=["GET", "POST"])
@login_required
def automations():
    db = get_db()
    if request.method == "POST":
        key = request.form.get("key") or ""
        on = request.form.get("on") == "1"
        try:
            A.set_enabled(db, g.user, key, on, request.form.get("note"))
            label = ("Everything" if key == "_all" else "Quiet hours" if key == "quiet_hours"
                     else A.BY_KEY[key]["name"])
            flash(f"{label}: {'switched back on' if on else 'paused'}.", "ok")
        except PermissionDenied:
            abort(403)
        except ValidationError as e:
            flash(_err(e), "error")
        return redirect(url_for("workflow.automations"))
    return render_template("automations.html", o=A.overview(db, current_app.config))


# -- Saved searches --------------------------------------------------------------------------------------------
def _list_url(kind: str, qs: str = "") -> str:
    return url_for("sellers.index" if kind == "seller" else "buyers.index") + (("?" + qs) if qs else "")


@bp.route("/saved-searches", methods=["POST"])
@require("crm.view")
def save_search():
    from urllib.parse import parse_qsl
    kind = request.form.get("kind") or ""
    qs = request.form.get("query") or ""
    try:
        SS.save(get_db(), g.user, kind, request.form.get("name"), dict(parse_qsl(qs)))
        flash("Search saved. You'll see how many new records match it each time you come back.", "ok")
    except ValidationError as e:
        flash(_err(e), "error")
    return redirect(_list_url(kind if kind in SS.KEYS else "seller", qs))


@bp.route("/saved-searches/<int:sid>")
@require("crm.view")
def open_search(sid):
    s = SS.open_search(get_db(), g.user, sid)
    if not s:
        abort(404)
    return redirect(_list_url(s["kind"], s["query"]))


@bp.route("/saved-searches/<int:sid>/delete", methods=["POST"])
@require("crm.view")
def delete_search(sid):
    db = get_db()
    s = db.one("SELECT kind FROM saved_searches WHERE id = ? AND user_id = ?", (sid, g.user["id"]))
    SS.delete(db, g.user, sid)
    flash("Saved search deleted.", "ok")
    return redirect(_list_url(s["kind"] if s else "seller"))


# -- Customer progress link ------------------------------------------------------------------------------------
def _opp(opp_id):
    o = SL.load(get_db(), opp_id)
    if not o:
        abort(404)
    return o


def _record_url(o):
    return url_for("buyers.detail" if o["kind"] == "buyer" else "sellers.detail", opp_id=o["id"])


@bp.route("/records/<int:opp_id>/progress-link", methods=["POST"])
@require("crm.log_activity")
def progress_link(opp_id):
    o = _opp(opp_id)
    try:
        token = SL.create(get_db(), g.user, o)
    except ValidationError as e:
        flash(_err(e), "error")
        return redirect(_record_url(o) + "#progress")
    link = messaging.public_base(request) + url_for("public_status.page", token=token)
    return render_template("progress_link_ready.html", o=o, link=link, back=_record_url(o))


@bp.route("/records/<int:opp_id>/progress-link/off", methods=["POST"])
@require("crm.log_activity")
def progress_link_off(opp_id):
    o = _opp(opp_id)
    SL.revoke(get_db(), g.user, o)
    flash("Progress link switched off — it no longer opens.", "ok")
    return redirect(_record_url(o) + "#progress")


@bp.route("/records/<int:opp_id>/progress-preview")
@require("crm.view")
def progress_preview(opp_id):
    """What the customer sees (in English or ?lang=te), for staff — nothing is counted as an open."""
    o = _opp(opp_id)
    return _progress_page(SL.view(get_db(), o), preview=True, back=_record_url(o))


def _progress_page(v, preview=False, back=None, status=200):
    lang = i18n.pick(request)
    _ = lambda s: i18n.tr(s, lang)  # noqa: E731
    resp = make_response(render_template("public/progress.html", v=v, lang=lang, LANGS=i18n.LANGS, _=_,
                                         preview=preview, back=back), status)
    if request.args.get("lang") in i18n.LANGS:
        resp.set_cookie("form_lang", lang, max_age=180 * 86400, samesite="Lax", httponly=True)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@public_bp.route("/p/<token>")
def page(token):
    db = get_db()
    link = SL.resolve(db, token)
    if not link:
        return _progress_page({"state": "missing"}, status=404)
    v = SL.view(db, SL.load(db, link["opportunity_id"]))
    return _progress_page(v, status=410 if v["state"] == "inactive" else 200)


@bp.app_context_processor
def _progress_extras():
    if (request.endpoint or "") not in ("sellers.detail", "buyers.detail") or not g.get("user"):
        return {}
    opp_id = (request.view_args or {}).get("opp_id")
    try:
        return {"progress_link": SL.current(get_db(), int(opp_id)) if opp_id else None}
    except Exception:  # a side card must never break the record page
        return {}


# -- Results by lead source ------------------------------------------------------------------------------------
@bp.route("/reports/sources")
@require("crm.view")
def sources():
    p = R.period_bounds(request.args.get("period", "month"), request.args.get("start"), request.args.get("end"))
    return render_template("reports/sources.html", r=R.sources(get_db(), g.user, p), p=p, PERIODS=R.PERIODS)


# -- Install on a phone (PWA) ----------------------------------------------------------------------------------
@bp.route("/install")
@login_required
def install():
    return render_template("install.html")


@pwa_bp.route("/manifest.webmanifest")
def manifest():
    body = {
        "name": "Salon Shastra TeamOS", "short_name": "TeamOS", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#fbf7f1", "theme_color": "#c04000",
        "description": "Salon Shastra team workspace: sellers, buyers, matches and deals.",
        "icons": [{"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png",
                   "purpose": "any"},
                  {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png",
                   "purpose": "any"},
                  {"src": url_for("static", filename="icons/maskable-512.png"), "sizes": "512x512",
                   "type": "image/png", "purpose": "maskable"}],
    }
    import json
    return Response(json.dumps(body), mimetype="application/manifest+json")


SW = """// TeamOS phone app. Privacy: only the app's look (styles, script, icons) and an offline notice are kept on the
// phone. Pages with customer information are never stored — they always come fresh from the server.
const CACHE = 'teamos-shell-v3';
const SHELL = ['/offline', '/static/app.css', '/static/app.js', '/static/icons/icon-192.png', '/static/icons/favicon-32.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  if (req.mode === 'navigate') { e.respondWith(fetch(req).catch(() => caches.match('/offline'))); return; }
  if (SHELL.includes(new URL(req.url).pathname)) {
    e.respondWith(fetch(req).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return r; })
      .catch(() => caches.match(req)));
  }
});
// Alerts: the push carries no data. Woken, the phone asks TeamOS (signed in) what is new and shows that.
self.addEventListener('push', e => {
  e.waitUntil(fetch('/me/push/latest', {credentials: 'include', cache: 'no-store'})
    .then(r => (r.ok && (r.headers.get('content-type') || '').includes('json')) ? r.json() : null)
    .catch(() => null)
    .then(d => {
      d = d || {title: 'Salon Shastra TeamOS', body: 'You have new updates. Tap to open.', url: '/'};
      return self.registration.showNotification(d.title, {body: d.body, icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png', tag: d.tag || 'teamos', renotify: true, data: {url: d.url || '/'}});
    }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', location.origin).href;
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    for (const c of list) { if (c.url.startsWith(location.origin) && 'focus' in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
"""


@pwa_bp.route("/sw.js")
def service_worker():
    resp = Response(SW, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@pwa_bp.route("/favicon.ico")
def favicon():
    """Browsers ask for /favicon.ico on their own: the Salon Shastra hat."""
    from flask import current_app, send_from_directory
    resp = send_from_directory(current_app.static_folder, "icons/favicon.ico", mimetype="image/x-icon")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


@pwa_bp.route("/offline")
def offline():
    return render_template("offline.html")
