"""An article pasted from elsewhere: its pictures copied safely, its HTML kept clean.

- /media/import-url copies a picture from a public web address into the
  member's own post media, and refuses anything that would reach inside the
  network (loopback, private ranges, cloud metadata), anything not http(s),
  credentials in the URL, and anything that is not a picture;
- an article body is reduced to the safe subset on the server whatever the
  client sent: scripts, handlers, javascript: links, unknown figures go; the
  structure (headings, lists, tables, quotes, code, rules, captions and
  alignment on figures) stays;
- an article placing an imported picture is published with it and reads back
  the same; editing it keeps it.

Needs the internet for the one public picture (skipped, not failed, offline).
"""
import random
import string
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True
PUBLIC_PICTURE = "https://www.gstatic.com/images/branding/googlelogo/1x/googlelogo_color_272x92dp.png"


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG"}
    r = c.post("/auth/register", json=body)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "handle": f"u{t}"}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


writer = register("Demo Writer")

print("Importing a picture: what is refused")
for label, url in [
    ("loopback", "http://127.0.0.1:8200/api/health"),
    ("localhost by name", "http://localhost/x.png"),
    ("cloud metadata", "http://169.254.169.254/latest/meta-data/"),
    ("a private range", "http://10.0.0.1/x.png"),
    ("a service next door", "http://media-service:8000/health"),
    ("not http", "file:///etc/passwd"),
    ("ftp", "ftp://example.com/x.png"),
    ("credentials in the address", "https://user:pass@example.com/x.png"),
    ("IPv6 loopback", "http://[::1]/x.png"),
]:
    r = c.post("/media/import-url", headers=H(writer), json={"url": url})
    check(f"{label} is refused", r.status_code == 422, f"{r.status_code} {r.text[:160]}")

r = c.post("/media/import-url", json={"url": PUBLIC_PICTURE})
check("signed out: refused", r.status_code == 401, r.status_code)

online = True
try:
    httpx.get(PUBLIC_PICTURE, timeout=8)
except httpx.HTTPError:
    online = False

imported = None
if online:
    r = c.post("/media/import-url", headers=H(writer), json={"url": "https://example.com/"})
    check("a page that is not a picture is refused", r.status_code == 422 and "supported" in r.text, r.text[:160])

    print()
    print("Importing a picture: a public one")
    r = c.post("/media/import-url", headers=H(writer), json={"url": PUBLIC_PICTURE, "alt_text": "A logo"})
    check("copied", r.status_code == 201, r.text[:200])
    imported = r.json() if r.status_code == 201 else None
    check("as the writer's own picture", imported and imported["id"].startswith("mda_") and imported["kind"] == "image",
          imported)
    check("with a preview address signed for them", imported and "&s=" in (imported.get("preview_url") or ""), imported)
    again = c.post("/media/import-url", headers=H(writer), json={"url": PUBLIC_PICTURE}).json()
    check("the same picture twice is stored once", imported and again.get("id") == imported["id"], again)
else:
    print("  SKIP the public picture (offline)")

print()
print("An article body is cleaned on the server")
figure = (
    f'<figure data-media-id="{imported["id"]}" data-kind="image" data-caption="The caption" '
    f'data-alt="A logo" data-align="wide" onclick="x()"><img src="https://evil.example/x.png"></figure>'
    if imported else ""
)
dirty = (
    "<h1>Title</h1><p>Intro <b>bold</b> <i>it</i> <u>under</u> <s>gone</s> H<sub>2</sub>O x<sup>2</sup></p>"
    '<script>alert(1)</script><style>p{}</style><p onclick="steal()" style="color:red">Styled</p>'
    '<a href="javascript:alert(1)">bad link</a> <a href="https://example.com/doc.pdf">good link</a>'
    "<ul><li>one<ul><li>nested</li></ul></li></ul><ol><li>first</li></ol>"
    "<blockquote>Quoted</blockquote><pre><code>let x = 1</code></pre><hr>"
    '<table><thead><tr><th colspan="2">Head</th></tr></thead><tbody><tr><td>a</td><td rowspan="99">b</td></tr></tbody></table>'
    '<img src="x" onerror="alert(1)"><iframe src="https://evil.example"></iframe>'
    '<figure data-embed="youtube" data-id="dQw4w9WgXcQ" data-caption="A video"></figure>'
    '<figure data-media-id="not-ours" data-kind="image"></figure>'
    "<p></p><p><br></p><p></p><p></p>" + figure + "<p>End</p>"
)
media = [{"media_id": imported["id"], "url": imported["url"], "kind": "image"}] if imported else []
r = c.post("/posts", headers=H(writer), json={"body": dirty, "format": "article", "visibility": "public",
                                               "media": media})
check("the article is published", r.status_code in (200, 201), r.text[:300])
post = r.json()
body = c.get(f"/posts/{post['id']}", headers=H(writer)).json()["body"]
for label, bad in [("no script", "<script"), ("no style element", "<style"), ("no handlers", "onclick"),
                   ("no onerror", "onerror"), ("no javascript: links", "javascript:"), ("no inline style", "style="),
                   ("no iframe", "<iframe"), ("no raw img", "<img"), ("no unknown figure", "not-ours")]:
    check(label, bad not in body, body[:400])
for label, good in [("h1 becomes the article's heading", "<h2>Title</h2>"), ("bold", "<b>bold</b>"),
                    ("italic", "<i>it</i>"), ("underline", "<u>under</u>"), ("strikethrough", "<s>gone</s>"),
                    ("subscript", "<sub>2</sub>"), ("superscript", "<sup>2</sup>"),
                    ("nested list", "<ul><li>one<ul><li>nested</li></ul></li></ul>"),
                    ("ordered list", "<ol><li>first</li></ol>"), ("quote", "<blockquote>Quoted</blockquote>"),
                    ("code", "<pre><code>let x = 1</code></pre>"), ("rule", "<hr>"),
                    ("table with its span", '<th colspan="2">Head</th>'),
                    ("a span past the limit is dropped", "<td>b</td>"),
                    ("a safe link, hardened", 'href="https://example.com/doc.pdf" rel="noopener noreferrer nofollow"'),
                    ("the bad link keeps its words", "bad link"),
                    ("the video embed with its caption", 'data-embed="youtube" data-id="dQw4w9WgXcQ" data-caption="A video"'),
                    ("empty paragraphs collapsed to one", "<p><br></p><figure") if imported else ("empty paragraphs collapsed", "<p><br></p><p>End")]:
    check(label, good in body, body[:600])
if imported:
    check("the picture keeps its caption, description and alignment",
          f'data-media-id="{imported["id"]}" data-kind="image" data-caption="The caption" data-alt="A logo" data-align="wide"></figure>' in body,
          body)
    attached = c.get(f"/posts/{post['id']}/media", headers=H(writer)).json()["media"]
    check("and is attached to the post", [m["media_id"] for m in attached] == [imported["id"]], attached)

print()
print("Edited, it stays clean and whole")
r = c.patch(f"/posts/{post['id']}", headers=H(writer),
            json={"body": body + '<p>Added <a href="data:text/html,x">x</a><svg onload="x()"></svg></p>',
                  "format": "article", "media": media})
check("saved", r.status_code == 200, r.text[:300])
edited = c.get(f"/posts/{post['id']}", headers=H(writer)).json()["body"]
check("the new dangerous parts are gone", "data:text" not in edited and "<svg" not in edited and "onload" not in edited,
      edited[-300:])
check("what was there is unchanged", edited.startswith(body), edited[:200])
if imported:
    attached = c.get(f"/posts/{post['id']}/media", headers=H(writer)).json()["media"]
    check("the picture is still attached", [m["media_id"] for m in attached] == [imported["id"]], attached)

print()
print("A post that is not an article is left as written, and stays short")
r = c.post("/posts", headers=H(writer), json={"body": "Plain <b>text</b> 5 < 6", "visibility": "public"})
check("published", r.status_code in (200, 201), r.text[:200])
check("its text untouched", c.get(f"/posts/{r.json()['id']}").json()["body"] == "Plain <b>text</b> 5 < 6")
r = c.post("/posts", headers=H(writer), json={"body": "x" * 20001, "visibility": "public"})
check("a text post past 20,000 characters is refused", r.status_code == 422, r.status_code)
long_article = "<p>" + ("word " * 9000) + "</p>"
r = c.post("/posts", headers=H(writer), json={"body": long_article, "format": "article", "visibility": "public"})
check("a long article (45,000 characters) is accepted", r.status_code in (200, 201), r.text[:200])

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
