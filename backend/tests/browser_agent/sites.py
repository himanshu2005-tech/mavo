# ruff: noqa: E501
"""Fake websites that behave like the awkward real ones Crew meet."""

import html
import json
from urllib.parse import parse_qs

HTML = "text/html; charset=utf-8"
COMPANIES = [
    "Acme",
    "Globex",
    "Initech",
    "Umbrella",
    "Hooli",
    "Stark",
    "Wayne",
    "Wonka",
    "Tyrell",
    "Cyberdyne",
]


def page(body, title="Page", head=""):
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>{head}</head><body>{body}</body></html>"


# ───────────────────────────── a placements portal (single-page app) ─────────────────────────────


def make_jobs(total=108):
    jobs = []
    for i in range(total):
        status = (
            "Open For Applications"
            if i % 9 == 0
            else "In Progress"
            if i % 7 == 1
            else "Closed For Applications"
        )
        jobs.append(
            {
                "title": f"{COMPANIES[i % 10]} Role {i + 1}",
                "apply_before": f"{1 + i % 28} Nov 2026",
                "status": status,
            }
        )
    return jobs


class Portal:
    """/jobs draws "0 Jobs Listed" first, fills the table after a moment, and paginates in place."""

    def __init__(self, total=108, logged_in=True, fill_delay_ms=1800, signed_out="front"):
        self.jobs, self.logged_in, self.fill_delay_ms = make_jobs(total), logged_in, fill_delay_ms
        # What a signed-out visitor meets: the front page, a human check first (as the real
        # portal does), or the jobs page saying the session ended.
        self.signed_out = signed_out

    def open_jobs(self):
        return [j for j in self.jobs if j["status"] == "Open For Applications"]

    def __call__(self, method, path, query, body):
        if path == "/captcha":
            return (
                200,
                HTML,
                page(
                    "<h1>Verify you're human</h1><p>Please complete the verification to continue to the login page.</p>",
                    "Check",
                ),
            )
        if path == "/login":
            return (
                200,
                HTML,
                page(
                    "<h1>Sign in</h1><input type='email' aria-label='Email'><input type='password' aria-label='Password'>",
                    "Sign in",
                ),
            )
        if path == "/jobs" and not self.logged_in and self.signed_out == "captcha":
            return 302, HTML, page("<script>location.replace('/captcha')</script>")
        if path == "/jobs" and not self.logged_in and self.signed_out == "expired":
            return (
                200,
                HTML,
                page(
                    "<nav><a href='/'>Home</a></nav><h1>Jobs</h1><p>Your session has timed out. Please sign in again.</p><a href='/login'>Log in</a>",
                    "Jobs",
                ),
            )
        if path == "/jobs" and not self.logged_in:
            return 302, HTML, page("<script>location.replace('/')</script>")
        if path == "/":
            return (
                200,
                HTML,
                page(
                    "<header><a href='/'>Home</a><a href='/signin'>Sign In</a></header><h1>Placements</h1><p>Welcome. Please sign in.</p>",
                    "Welcome",
                ),
            )
        if path == "/jobs":
            return (
                200,
                HTML,
                page(
                    f"""<nav><a href="/">Home</a><a href="/jobs">Jobs</a><a href="/profile">Profile</a></nav>
<h1>Jobs</h1><div id="count">0 Jobs Listed</div>
<label>Per page <select id="per" aria-label="Per page"><option>10</option><option selected>20</option><option>50</option></select></label>
<table><thead><tr><th>Title</th><th>Apply Before</th><th>Status</th></tr></thead><tbody id="rows"></tbody></table>
<div><button id="prev">Previous</button> <span id="pg"></span> <button id="next">Next</button></div>
<script>
const JOBS = {json.dumps(self.jobs)};
let loaded = false, pageNo = 1, per = 20;
function render() {{
  const pages = Math.max(1, Math.ceil(JOBS.length / per));
  document.getElementById('count').textContent = loaded ? JOBS.length + ' Jobs Listed' : '0 Jobs Listed';
  const rows = loaded ? JOBS.slice((pageNo - 1) * per, pageNo * per) : [];
  document.getElementById('rows').innerHTML = rows.map(j => '<tr><td>' + j.title + '</td><td>' + j.apply_before + '</td><td>' + j.status + '</td></tr>').join('');
  document.getElementById('pg').textContent = loaded ? 'Page ' + pageNo + ' of ' + pages : '';
  document.getElementById('prev').disabled = pageNo <= 1;
  document.getElementById('next').disabled = pageNo >= pages;
}}
document.getElementById('next').onclick = () => {{ pageNo++; render(); }};
document.getElementById('prev').onclick = () => {{ pageNo--; render(); }};
document.getElementById('per').onchange = e => {{ per = +e.target.value; pageNo = 1; render(); }};
render(); setTimeout(() => {{ loaded = true; render(); }}, {self.fill_delay_ms});
</script>""",
                    "Jobs",
                ),
            )
        return 404, HTML, page("Not found")


# ───────────────────────────── an application form ─────────────────────────────

FIELDS = """
<h1>Apply</h1>
<form method="post" action="/submit">
<label for="name">Full name</label><input id="name" name="name" type="text">
<label for="email">Email address</label><input id="email" name="email" type="email">
<label for="phone">Phone</label><input id="phone" name="phone" type="tel">
<label for="branch">Branch</label><select id="branch" name="branch"><option value="">Choose</option><option>CSE</option><option>ECE</option><option>ME</option></select>
<fieldset><legend>Gender</legend>
<label><input type="radio" name="gender" value="male"> Male</label>
<label><input type="radio" name="gender" value="female"> Female</label>
<label><input type="radio" name="gender" value="other"> Other</label></fieldset>
<fieldset><legend>Skills</legend>
<label><input type="checkbox" name="skills" value="python"> Python</label>
<label><input type="checkbox" name="skills" value="sql"> SQL</label>
<label><input type="checkbox" name="skills" value="java"> Java</label></fieldset>
<label for="cgpa">CGPA</label><input id="cgpa" name="cgpa" type="number" step="0.01">
<label for="grad">Graduation date</label><input id="grad" name="grad" type="date">
<label for="cover">Cover letter</label><textarea id="cover" name="cover"></textarea>
<label for="resume">Resume</label><input id="resume" name="resume" type="file">
<label for="pw">Portal password</label><input id="pw" name="pw" type="password">
<label><input type="checkbox" name="consent" value="yes"> I confirm the details are correct</label>
<button type="submit">Submit application</button>
</form>"""


class Forms:
    def __init__(self):
        self.submissions = []

    def __call__(self, method, path, query, body):
        if path == "/apply":
            return 200, HTML, page(FIELDS, "Apply")
        if path == "/submit" and method == "POST":
            data = {
                k: v if len(v) > 1 else v[0]
                for k, v in parse_qs(body, keep_blank_values=True).items()
            }
            errors = []
            if not data.get("name"):
                errors.append("Full name is required")
            if "@" not in data.get("email", ""):
                errors.append("Enter a valid email")
            if data.get("consent") != "yes":
                errors.append("You must confirm the details")
            if errors:
                return (
                    200,
                    HTML,
                    page(
                        "<h1>Fix these</h1><ul>"
                        + "".join(f"<li>{html.escape(e)}</li>" for e in errors)
                        + "</ul><a href='/apply'>Back to the form</a>",
                        "Errors",
                    ),
                )
            self.submissions.append(data)
            return (
                200,
                HTML,
                page(
                    f"<h1>Application received</h1><p id='ref'>Reference #{len(self.submissions)}</p>",
                    "Done",
                ),
            )
        return 404, HTML, page("Not found")


# ───────────────────────────── a multi-step wizard with dependent fields ─────────────────────────────

WIZARD = """
<h1 id="title">Book a slot</h1><div id="step"></div><div id="out"></div>
<script>
const CITIES = {'Tamil Nadu': ['Chennai', 'Madurai'], 'Karnataka': ['Bengaluru', 'Mysuru']};
let step = 1, state = '', city = '';
function draw() {
  const el = document.getElementById('step');
  if (step === 1) el.innerHTML = '<label>State <select id="s" aria-label="State"><option value="">Choose</option><option>Tamil Nadu</option><option>Karnataka</option></select></label><button id="n1" disabled>Next</button>';
  if (step === 2) el.innerHTML = '<label>City <select id="c" aria-label="City"><option value="">Choose</option>' + CITIES[state].map(c => '<option>' + c + '</option>').join('') + '</select></label><button id="b2">Back</button><button id="n2" disabled>Next</button>';
  if (step === 3) el.innerHTML = '<p id="review">Review: ' + state + ' / ' + city + '</p><button id="b3">Back</button><button id="ok">Confirm booking</button>';
  if (step === 1) { s.onchange = e => { state = e.target.value; n1.disabled = !state; }; n1.onclick = () => { step = 2; draw(); }; }
  if (step === 2) { c.onchange = e => { city = e.target.value; n2.disabled = !city; }; n2.onclick = () => { step = 3; draw(); }; b2.onclick = () => { step = 1; draw(); }; }
  if (step === 3) { b3.onclick = () => { step = 2; draw(); }; ok.onclick = () => { document.getElementById('step').innerHTML = ''; document.getElementById('out').textContent = 'Booked: ' + state + ' / ' + city; }; }
}
draw();
</script>"""


def wizard(method, path, query, body):
    return 200, HTML, page(WIZARD, "Wizard")


# ───────────────────────────── a shop: cookie wall, custom dropdown, shadow DOM ─────────────────────────────

SHOP = """
<div id="banner" style="position:fixed;inset:0;background:#000c;z-index:9;display:grid;place-items:center">
  <div style="background:#fff;padding:24px"><p>We use cookies.</p><button id="accept" onclick="document.getElementById('banner').remove()">Accept all</button></div>
</div>
<h1>Shop</h1>
<div><button id="size" aria-haspopup="listbox" aria-expanded="false" onclick="
  const l = document.getElementById('sizes'); l.hidden = !l.hidden; this.setAttribute('aria-expanded', String(!l.hidden))">Size: <span id="chosen">none</span></button>
  <ul id="sizes" role="listbox" hidden><li role="option" onclick="pick('S')">S</li><li role="option" onclick="pick('M')">M</li><li role="option" onclick="pick('L')">L</li></ul></div>
<button id="add" onclick="n++; cart.textContent = n + ' in cart'">Add to cart</button> <span id="cart">0 in cart</span>
<mavo-coupon></mavo-coupon>
<script>
let n = 0; const cart = document.getElementById('cart');
function pick(s) { document.getElementById('chosen').textContent = s; document.getElementById('sizes').hidden = true; }
customElements.define('mavo-coupon', class extends HTMLElement {
  constructor() { super(); const r = this.attachShadow({mode: 'open'});
    r.innerHTML = '<label>Coupon <input aria-label="Coupon code"></label><button id="go">Apply coupon</button><p id="msg"></p>';
    r.getElementById('go').onclick = () => { r.getElementById('msg').textContent = r.querySelector('input').value === 'SAVE10' ? 'Coupon applied: 10% off' : 'Invalid coupon'; }; }
});
</script>"""


def shop(method, path, query, body):
    return 200, HTML, page(SHOP, "Shop")


# ───────────────────────────── an account area behind a hidden menu ─────────────────────────────


class Account:
    def __init__(self, balance="$0.15", logged_in=True):
        self.balance, self.logged_in = balance, logged_in

    def __call__(self, method, path, query, body):
        if path == "/":
            return (
                200,
                HTML,
                page(
                    """<header><a href="/">Home</a><a href="/pricing">Pricing</a>
<button id="av" aria-haspopup="menu" aria-expanded="false" onclick="
  m.hidden = false; this.setAttribute('aria-expanded', 'true')"><img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></button>
<nav id="m" hidden><a href="/settings">Settings</a><a href="/billing/credits">Billing &amp; Credits</a><a href="/logout">Sign out</a>
<button onclick="document.title = 'ACCOUNT DELETED'">Delete account</button></nav></header><h1>Dashboard</h1>"""
                    if self.logged_in
                    else "<header><a href='/pricing'>Pricing</a><a href='/signin'>Sign In</a></header><h1>One API</h1>",
                    "Home",
                ),
            )
        if path == "/billing/credits":
            if not self.logged_in:
                return 200, HTML, page("<script>location.replace('/')</script>")
            return (
                200,
                HTML,
                page(
                    f"<h1>Credits</h1><p>Current balance: <b id='bal'>{self.balance}</b></p>",
                    "Credits",
                ),
            )
        if path in {"/pricing", "/settings"}:
            return 200, HTML, page(f"<h1>{path[1:].title()}</h1>", path[1:].title())
        return 404, HTML, page("Not found")


# ───────────────────────────── lazy lists, slow and flaky pages, frames, tabs ─────────────────────────────

FEED = """<h1>Feed</h1><ul id="list"></ul>
<script>
const list = document.getElementById('list'); let n = 0;
function more() { for (let i = 0; i < 10 && n < 50; i++) { const li = document.createElement('li'); li.textContent = 'Item ' + (++n); li.style.height = '120px'; list.appendChild(li); } }
more();
window.addEventListener('scroll', () => { if (innerHeight + scrollY > document.body.scrollHeight - 200) more(); });
document.body.style.minHeight = '400px';
</script>"""


def feed(method, path, query, body):
    return 200, HTML, page(FEED, "Feed")


def slow(method, path, query, body):
    return 200, HTML, page("<h1>Finally</h1><p>The slow page loaded.</p>", "Slow"), 6


class Flaky:
    """Shows its own error the first time, then works: the page-level failure real SPAs have."""

    def __init__(self):
        self.visits = 0

    def __call__(self, method, path, query, body):
        self.visits += 1
        content = (
            "<p>Failed to load jobs.</p>"
            if self.visits == 1
            else "<h1>Jobs</h1><p>3 Jobs Listed</p><table><tr><th>Title</th></tr><tr><td>One</td></tr><tr><td>Two</td></tr><tr><td>Three</td></tr></table>"
        )
        return 200, HTML, page(content, "Jobs")


def frames(method, path, query, body):
    if path == "/inner":
        return (
            200,
            HTML,
            page(
                "<label>Card number <input aria-label='Card number'></label><button id='pay' onclick=\"document.body.append('Paid')\">Pay now</button>",
                "Inner",
            ),
        )
    return (
        200,
        HTML,
        page(
            "<h1>Checkout</h1><iframe src='/inner' width='500' height='150' title='Payment'></iframe>",
            "Outer",
        ),
    )


TABS = """<div role="tablist"><button role="tab" aria-selected="true" id="t1">Overview</button><button role="tab" aria-selected="false" id="t2">Deadlines</button></div>
<div id="p1">Overview text</div><div id="p2" hidden>Deadline: 3 Nov 2026 at 5 PM</div>
<button id="acc" aria-expanded="false" onclick="d.hidden = !d.hidden; this.setAttribute('aria-expanded', String(!d.hidden))">More info</button><div id="d" hidden>Hidden details: apply via the portal</div>
<script>t2.onclick = () => { p1.hidden = true; p2.hidden = false; t1.setAttribute('aria-selected','false'); t2.setAttribute('aria-selected','true'); }; t1.onclick = () => { p1.hidden = false; p2.hidden = true; };</script>"""


def tabs(method, path, query, body):
    return 200, HTML, page(TABS, "Tabs")


def external_links(method, path, query, body):
    return (
        200,
        HTML,
        page(
            "<a href='https://evil.test/' target='_blank'>Offsite (new tab)</a><a href='https://evil.test/'>Offsite</a><a href='/inside'>Inside</a>",
            "Links",
        ),
    )


def hover_menu(method, path, query, body):
    if path == "/account/billing":
        return 200, HTML, page("<h1>Billing</h1><p>Plan: Pro</p>", "Billing")
    return (
        200,
        HTML,
        page(
            """<style>.m{position:relative;display:inline-block}.m ul{display:none;position:absolute;top:100%;left:0;margin:0;padding:8px;background:#222;list-style:none}.m:hover ul{display:block}</style>
<header><a href="/">Home</a> <div class="m"><a href="#" onclick="return false">Account</a><ul><li><a href="/account/billing">Billing</a></li><li><a href="/account/profile">Profile</a></li></ul></div></header><h1>Welcome</h1>""",
            "Hover",
        ),
    )


def search_site(method, path, query, body):
    return (
        200,
        HTML,
        page(
            """<h1>Search</h1><input aria-label="Query" id="q"><div id="out"></div>
<script>q.addEventListener('keydown', e => { if (e.key === 'Enter') { out.textContent = 'Searching...'; setTimeout(() => out.textContent = 'Results for ' + q.value + ': 3 matches', 900); } });</script>""",
            "Search",
        ),
    )


class Confirming:
    """A form whose button asks 'Are you sure?' first; and a second one that asks to delete."""

    def __init__(self):
        self.sent = 0

    def __call__(self, method, path, query, body):
        if method == "POST":
            self.sent += 1
            return 200, HTML, page("<h1>Sent</h1>", "Sent")
        return (
            200,
            HTML,
            page(
                """<form method="post" onsubmit="return confirm('Submit your application?')"><button>Send application</button></form>
<button onclick="if (confirm('Delete your account?')) document.title = 'DELETED'">Close account</button>""",
                "Confirm",
            ),
        )
