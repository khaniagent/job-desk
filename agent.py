"""Remote job agent. Fetches live feeds, filters for jobs open to Pakistan-based
applicants, ranks US companies first, writes a truthful message per job, builds docs/index.html.
No API keys needed. Runs on GitHub Actions every 3 hours."""
import json, re, time, html, sys, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

P = json.load(open(Path(__file__).parent / "profile.json"))
UA = {"User-Agent": "Mozilla/5.0 (job-agent; personal use)"}
NOW = time.time()

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")

def strip(t): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()

# ---------- sources ----------
def himalayas():
    out = []
    for q in ["next.js", "react developer", "full stack", "wordpress", "shopify", "node.js"]:
        try:
            d = json.loads(get("https://himalayas.app/jobs/api/search?sort=recent&q=" + urllib.parse.quote(q)))
        except Exception as e:
            print("himalayas fail", q, e, file=sys.stderr); continue
        for j in d.get("jobs", []):
            locs = j.get("locationRestrictions") or []
            low = [l.lower() for l in locs]
            if locs and "pakistan" not in low:  # restricted to other countries
                continue
            exp = j.get("expiryDate")
            if exp and float(exp) < NOW: continue
            out.append(dict(source="Himalayas", title=j.get("title", ""), company=j.get("companyName", ""),
                url=j.get("applicationLink") or j.get("guid", ""), pay=pay(j.get("minSalary"), j.get("maxSalary")),
                posted=j.get("pubDate"), deadline=exp, where="Worldwide" if not locs else "Pakistan listed",
                text=strip(j.get("description") or j.get("excerpt", "")), apply="Himalayas sign-up, then Apply now"))
    return out

def pay(a, b):
    return f"${int(a):,}-${int(b):,}/yr" if a and b else "Not listed"

def remoteok():
    out = []
    try:
        d = json.loads(get("https://remoteok.com/api"))
    except Exception as e:
        print("remoteok fail", e, file=sys.stderr); return out
    for j in d[1:]:
        loc = (j.get("location") or "").lower()
        if loc and not any(w in loc for w in ["worldwide", "anywhere", "global", "remote", "pakistan"]):
            continue
        out.append(dict(source="Remote OK", title=j.get("position", ""), company=j.get("company", ""),
            url=j.get("url") or j.get("apply_url", ""), pay=pay(j.get("salary_min"), j.get("salary_max")),
            posted=j.get("epoch"), deadline=None, where=j.get("location") or "Not stated",
            text=strip(j.get("description", "")) + " " + " ".join(j.get("tags") or []),
            apply="Apply on the Remote OK page"))
    return out

def wwr():
    out = []
    for feed in ["remote-full-stack-programming-jobs", "remote-front-end-programming-jobs", "remote-back-end-programming-jobs"]:
        try:
            root = ET.fromstring(get(f"https://weworkremotely.com/categories/{feed}.rss"))
        except Exception as e:
            print("wwr fail", feed, e, file=sys.stderr); continue
        for it in root.iter("item"):
            g = lambda n: (it.findtext(n) or "").strip()
            region = g("region")
            if region and not re.search(r"anywhere|worldwide", region, re.I): continue
            title = g("title"); comp, _, ttl = title.partition(": ")
            out.append(dict(source="We Work Remotely", title=ttl or title, company=comp if ttl else "",
                url=g("link"), pay="See listing", posted=None, deadline=None, where=region or "Anywhere",
                text=strip(g("description")), apply="Apply on the We Work Remotely page"))
    return out

# ---------- scoring ----------
US_HINT = re.compile(r"\b(united states|u\.s\.|usa|us[- ]based|us time ?zones?|eastern time|pacific time|est\b|pst\b|new york|san francisco|austin|florida|texas|california)\b", re.I)

def score(j):
    t = (j["title"] + " " + j["text"]).lower()
    title = j["title"].lower()
    if any(k in title for k in P["avoid_keywords"]): return None
    hits = [k for k in P["target_keywords"] if k in t]
    if not hits: return None
    s = len(hits) * 2 + sum(3 for k in P["target_keywords"] if k in title)
    m = re.findall(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", t)
    yrs = max([int(x) for x in m], default=0)
    if yrs > P["max_years_required"]: s -= 6
    gaps = sorted({k for k in P["not_proven"] if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t)})
    s -= len(gaps)
    j["us"] = bool(US_HINT.search(j["text"]))
    if j["us"]: s += 4
    j.update(score=s, years=yrs, gaps=gaps, hits=hits)
    return s

def message(j):
    t = (j["title"] + " " + j["text"]).lower()
    prov = [k for k in P["proven_skills"] if k in t]
    skills = ", ".join(dict.fromkeys(s.replace("next.js", "Next.js").replace("node.js", "Node.js").replace("react", "React").replace("rest api", "REST API") for s in prov[:5])) \
        or "Next.js, React and Node.js"
    gap = ""
    if j["gaps"]:
        gap = f"\n\nMy production work has been in {', '.join(['Next.js','React','Node.js','Laravel'])} rather than {', '.join(j['gaps'][:3])}; I'm a fast learner and happy to ramp up."
    co = j["company"] or "team"
    return (f"Hi {co},\n\nI'm applying for the {j['title']} role. I'm a full-stack developer with {P['years']} years of professional "
            f"experience. The work in your listing matches what I do: {skills}, plus API integrations, authentication and database-driven features. "
            f"Recent projects include an AI communication platform (Next.js, Node.js) and a React/Next.js CRM with dashboards.{gap}\n\n"
            f"{P['client_line']}, and I can start immediately. Portfolio: {P['portfolio']}\n\n{P['name']}")

def main():
    jobs = himalayas() + remoteok() + wwr()
    seen, ranked = set(), []
    for j in jobs:
        key = (j["company"].lower(), j["title"].lower())
        if key in seen or not j["url"]: continue
        seen.add(key)
        if score(j) is None: continue
        j["msg"] = message(j); j["id"] = re.sub(r"\W+", "", j["url"])[-40:]
        j["text"] = ""  # keep file small
        ranked.append(j)
    ranked.sort(key=lambda j: -j["score"])
    ranked = ranked[:60]
    page = (Path(__file__).parent / "template.html").read_text()
    stamp = time.strftime("%d %b %Y %H:%M UTC", time.gmtime())
    page = page.replace("__JOBS__", json.dumps(ranked)).replace("__STAMP__", stamp)
    out = Path(__file__).parent / "docs"; out.mkdir(exist_ok=True)
    (out / "index.html").write_text(page)
    print(f"{len(ranked)} jobs written from {len(jobs)} fetched")

if __name__ == "__main__":
    main()
