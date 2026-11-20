"""Collect open US early-career roles from configured public employer boards."""
import argparse
import concurrent.futures
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "site/data/jobs.json"
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true",
}
TRACKS = {
    "Software engineering": r"software|developer|frontend|backend|full.stack|computer science",
    "AI / ML": r"machine learning|artificial intelligence|deep learning|\bai\b|\bml\b|nlp|computer vision",
    "Data": r"data (?:sci|eng|analy)|analytics|business intelligence",
    "Systems and infrastructure": r"systems|infrastructure|platform|devops|site reliability|embedded|firmware|network",
    "Security": r"security|cyber",
    "Research": r"research|scientist|quantitative",
}
US_LOCATION = re.compile(
    r"\b(?:United States|USA|U\.S\.|US|New York|San Francisco|Bay Area|Seattle|Austin|Boston|Chicago|"
    r"Los Angeles|Mountain View|Palo Alto|Sunnyvale|San Jose|Atlanta|Denver|Washington DC)\b|"
    r"(?:,\s*|\()(?:(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    r"MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY))\b"
)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(html.unescape(value or ""))
    return " ".join(" ".join(parser.parts).split())


def iso_date(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or dt.timezone.utc).isoformat()
    except (AttributeError, ValueError):
        return None


def evidence(text, positive, negative):
    denied = re.search(negative, text, re.I)
    allowed = re.search(positive, text, re.I)
    if denied and allowed and not (denied.start() <= allowed.start() < denied.end()):
        return {"status": "Unclear", "quote": f"{denied.group()} / {allowed.group()}"}
    match = denied or allowed
    return {
        "status": "Restricted" if denied else "Explicitly supported" if allowed else "Unclear",
        "quote": match.group().strip() if match else "",
    }


def classify(title, employment, description):
    role = f"{title} {employment}"
    if re.search(r"\bco[- ]?op\b|cooperative education", role, re.I):
        kind = "Co-op"
    elif re.search(r"\bintern(?:ship)?\b", role, re.I):
        kind = "Internship"
    elif re.search(r"new grad|early career|entry[ -]level|university grad|graduate engineer|engineer i\b|engineer,? associate", title, re.I):
        kind = "Full-time"
    else:
        return None, []
    tracks = [name for name, pattern in TRACKS.items() if re.search(pattern, title, re.I)]
    if not tracks and kind != "Full-time" and not re.search(r"\b(?:MBA|finance|mechanical engineer|electrical engineer|product design|sales|marketing|recruiting)\b", title, re.I):
        tracks = [name for name, pattern in TRACKS.items() if re.search(pattern, description[:1800], re.I)]
    if kind == "Full-time" and re.search(r"\bsenior\b|\bstaff\b|\bprincipal\b|\bmanager\b|\bdirector\b|\blead\b", title, re.I):
        return None, []
    return kind, tracks


def normalize(provider, company, row, now):
    categories = row.get("categories") or {}
    if provider == "ashby" and row.get("isListed") is False:
        return None
    title = row.get("title") or row.get("text") or ""
    location = row.get("location") or categories.get("location") or ""
    if isinstance(location, dict):
        location = location.get("name") or ""
    extra = [item.get("location", "") for item in row.get("secondaryLocations") or []]
    locations = list(dict.fromkeys(filter(None, [location, *extra, *(categories.get("allLocations") or [])])))
    apply = row.get("applyUrl") or row.get("absolute_url") or row.get("hostedUrl") or row.get("jobUrl")
    if not title or not apply or urlsplit(apply).scheme != "https":
        return None
    description = plain(row.get("descriptionPlain") or row.get("description") or row.get("content"))
    kind, tracks = classify(title, row.get("employmentType") or categories.get("commitment") or "", description)
    if not kind or not tracks:
        return None
    text = f"{title}. {description}"
    term = re.search(r"\b(?:summer[ ,/-]+2027|2027[ ,/-]+summer)\b", text, re.I)
    other_term = re.search(r"\b(?:(?:spring|fall|winter)[ ,/-]+20\d\d|summer[ ,/-]+20(?:26|28|29))\b", title, re.I)
    degree = re.search(r"\b(?:master[’']?s|masters|M\.?S\.?|graduate student|graduate degree)\b", title, re.I)
    phd_only = bool(re.search(r"\bPh\.?D\b", title, re.I) and not degree)
    degree = degree or re.search(r"\b(?:master[’']?s|masters|graduate student|graduate degree)\b", description, re.I)
    graduation = re.search(r"[^.!?]{0,35}\bgraduat(?:e|ing|ion)\b[^.!?]{0,85}\b20\d{2}\b", description, re.I)
    deadline = re.search(r"\b(?:apply by|applications? (?:close|deadline|due))\s*[:–-]?\s*([^.!?]{5,75})", description, re.I)
    clearance = re.search(r"\b(?:U\.?S\.? citiz(?:en|enship)|security clearance|ITAR|export control)[^.!?]{0,85}", description, re.I)
    req = row.get("requisition_id")
    parsed_url = urlsplit(row.get("jobUrl") or row.get("hostedUrl") or apply)
    key = f"{company}:{req}" if req else f"{parsed_url.netloc}{parsed_url.path.rstrip('/')}"
    workplace = row.get("workplaceType") or ("Remote" if row.get("isRemote") else "")
    if not workplace:
        label = re.search(r"\b(remote|hybrid|on[ -]?site)\b", " ".join(locations), re.I)
        workplace = label.group().title() if label else "Unclear"
    return {
        "id": key, "title": title, "company": company, "locations": locations,
        "us": bool(US_LOCATION.search(" ".join(locations)) or (row.get("address") or {}).get("postalAddress", {}).get("addressCountry") in ("US", "USA")),
        "kind": kind, "tracks": tracks, "workplace": workplace.title(),
        "term": "Summer 2027" if term else "Other term" if other_term else "Unclear",
        "degree": degree.group() if degree else "", "phd_only": phd_only,
        "graduation": graduation.group().strip() if graduation else "",
        "deadline": deadline.group(1).strip() if deadline else "",
        "clearance": clearance.group() if clearance else "",
        "cpt": evidence(description, r"\b(?:accepts?|supports?|eligible for)\s+(?:F-1\s+)?CPT\b|\bCPT (?:is |are )?(?:accepted|supported|eligible)\b",
                        r"\b(?:no|do not accept|does not accept|cannot support)\s+(?:F-1\s+)?CPT\b|\bCPT (?:is |are )?not (?:accepted|supported|eligible)\b"),
        "sponsorship": evidence(description, r"\b(?:we (?:offer|provide)|eligible for)\s+(?:visa\s+|employment\s+)?sponsorship\b|\bvisa sponsorship (?:is )?available\b",
                                r"\b(?:without|no|do not offer|does not offer|unable to provide|cannot provide)\s+(?:[\w-]+\s+){0,3}sponsorship\b[^.!?]{0,75}|\b(?:visa )?sponsorship (?:is |will be )?not (?:available|provided|offered)\b[^.!?]{0,50}"),
        "url": apply, "provider": provider,
        "posted_at": iso_date(row.get("publishedAt")), "updated_at": iso_date(row.get("updated_at")),
        "pay": (row.get("compensation") or {}).get("compensationTierSummary") or (row.get("salaryRange") or {}).get("salaryDescription") or "",
        "first_seen_at": now, "last_seen_at": now, "status": "Open", "misses": 0,
    }


def fetch(source, now):
    provider, company, slug = source
    url = ENDPOINTS[provider].format(slug)
    for attempt in range(2):
        try:
            request = Request(url, headers={"User-Agent": "InternshipWatch/1.0", "Accept": "application/json"})
            with urlopen(request, timeout=15) as response:
                body = response.read(25_000_001)
            if len(body) > 25_000_000:
                raise ValueError("response exceeds size limit")
            payload = json.loads(body)
            rows = payload if provider == "lever" else payload["jobs"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) or not (row.get("id") or row.get("jobUrl")) for row in rows):
                raise ValueError("unexpected feed schema")
            if provider == "greenhouse" and payload.get("meta", {}).get("total", len(rows)) != len(rows):
                raise ValueError("incomplete feed")
            jobs = [job for row in rows if (job := normalize(provider, company, row, now))]
            return jobs, len(rows), None
        except HTTPError as error:
            if error.code < 500:
                return [], 0, f"HTTP {error.code}"
            message = f"HTTP {error.code}"
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            message = type(error).__name__
        if attempt == 0:
            time.sleep(2)
    return [], 0, message


def update(previous, results, now):
    sources = {name: health.copy() for name, health in previous.get("sources", {}).items()}
    jobs = {job["id"]: job for job in previous.get("jobs", [])}
    successes = 0
    for (_, company, _), (found, count, error) in results:
        health = sources.setdefault(company, {})
        health.update(checked_at=now, error=error)
        if error:
            continue
        successes += 1
        health.update(succeeded_at=now, count=count, matching=len(found))
        seen = {item["id"] for item in found}
        for item in found:
            historical = jobs.get(item["id"])
            if historical:
                item["first_seen_at"] = historical["first_seen_at"]
                item["baseline"] = historical.get("baseline", False)
                if historical["status"] == "Closed":
                    item["reopened_at"] = now
                elif historical.get("reopened_at"):
                    item["reopened_at"] = historical["reopened_at"]
            else:
                item["baseline"] = not previous.get("sources", {}).get(company, {}).get("succeeded_at")
            jobs[item["id"]] = item
        for item in jobs.values():
            if item["company"] == company and item["id"] not in seen and item["status"] == "Open":
                item["misses"] += 1
                if item["misses"] >= 2:
                    item.update(status="Closed", closed_at=now)
    cutoff = (dt.datetime.fromisoformat(now) - dt.timedelta(days=90)).isoformat()
    return {
        "checked_at": now,
        "last_success_at": now if successes else previous.get("last_success_at"),
        "sources": sources,
        "jobs": sorted((job for job in jobs.values() if job.get("closed_at", now) >= cutoff), key=lambda job: job["id"]),
    }, successes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Check just one company")
    args = parser.parse_args()
    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    config = json.loads((ROOT / "sources.json").read_text())
    sources = [(provider, company, slug) for provider, boards in config.items()
               for company, slug in boards.items() if not args.only or company == args.only]
    if not sources:
        parser.error("No matching sources")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(zip(sources, pool.map(lambda source: fetch(source, now), sources)))
    snapshot, successes = update(previous, results, now)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, OUTPUT)
    for (_, company, _), (jobs, count, error) in results:
        print(f"{company}: {error or f'{len(jobs)} matching of {count}'}")
    print(f"{successes}/{len(sources)} boards succeeded")
    return 0 if successes else 1


if __name__ == "__main__":
    sys.exit(main())
