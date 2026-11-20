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

import national

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "site/data/jobs.json"
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{}/postings?limit=100&country=us&q=intern",
}
TRACKS = {
    "Software engineering": r"software|developer|frontend|backend|full.stack|computer science",
    "AI / ML": r"machine learning|artificial intelligence|deep learning|\bai\b|\bml\b|nlp|computer vision",
    "Data": r"data (?:sci|eng|analy)|analytics|business intelligence",
    "Systems and infrastructure": r"systems|infrastructure|platform|devops|site reliability|embedded|firmware|network",
    "Security": r"security|cyber",
    "IT and technology": r"information technology|technology analyst|IT intern|quality assurance|technical support|computer engineer",
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
    if not kind or (kind == "Full-time" and not tracks):
        return None
    text = f"{title}. {description}"
    term = re.search(r"\b(?:summer[ ,/-]+2027|2027[ ,/-]+summer)\b", text, re.I)
    other_term = re.search(r"\b(?:(?:spring|fall|winter)[ ,/-]+20\d\d|summer[ ,/-]+20(?:26|28|29))\b", title, re.I)
    summer_dates = re.search(
        r"\b(?:may|june|july)\s+\d{1,2}(?:,?\s+2027)?\s*(?:through|to|[-–—])\s*"
        r"(?:august|september)\s+\d{1,2},?\s+2027\b", description, re.I)
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
        "term": "Other term" if other_term and not re.search(r"summer[ ,/-]+2027|2027[ ,/-]+summer", title, re.I) else "Summer 2027" if term or summer_dates else "Unclear",
        "degree": degree.group() if degree else "", "phd_only": phd_only,
        "graduation": graduation.group().strip() if graduation else "",
        "deadline": (row.get("deadline") or "")[:10] or (deadline.group(1).strip() if deadline else ""),
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


def smartrecruiters_row(slug, item):
    location = item.get("location") or {}
    return {
        "id": item["id"], "title": item["name"],
        "jobUrl": f"https://jobs.smartrecruiters.com/{slug}/{item['id']}",
        "location": ", ".join(filter(None, [location.get("city"), location.get("region"), location.get("country")])),
        "address": {"postalAddress": {"addressCountry": (location.get("country") or "").upper()}},
        "employmentType": (item.get("typeOfEmployment") or {}).get("label"),
        "publishedAt": item.get("releasedDate"),
        "isRemote": location.get("remote"),
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
            if provider == "smartrecruiters":
                rows = list(payload["content"])
                total = payload["totalFound"]
                endpoint = ENDPOINTS[provider].format(slug)
                while len(rows) < total and len(rows) < 500:
                    page = national.get_json(f"{endpoint}&offset={len(rows)}")["content"]
                    if not page:
                        raise ValueError("incomplete posting list")
                    rows.extend(page)
                if len(rows) != total:
                    raise ValueError("posting limit exceeded")
                rows = [smartrecruiters_row(slug, item) for item in rows]
            else:
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
    for (provider, company, _), (found, count, error) in results:
        health = sources.setdefault(company, {})
        health.update(checked_at=now, error=error, provider=provider)
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
            item["source_key"] = company
            jobs[item["id"]] = item
        for item in jobs.values():
            if item.get("source_key", item["company"]) == company and item["id"] not in seen and item["status"] == "Open":
                if provider in ("adzuna", "usajobs") and dt.datetime.fromisoformat(item["last_seen_at"]) > dt.datetime.fromisoformat(now) - dt.timedelta(days=2):
                    continue
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


def due(timestamp, now, seconds):
    return not timestamp or (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(timestamp)).total_seconds() >= seconds


def collect_national(provider, now):
    try:
        rows = list(national.national_rows(provider))
        jobs = [job for company, row in rows if (job := normalize(provider, company, row, now))]
        return jobs, len(rows), None
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as error:
        return [], 0, f"HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Check just one company")
    parser.add_argument("--scheduled", action="store_true", help="Check priority sources and rotate through the larger registry")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Write and restore state from this snapshot")
    args = parser.parse_args()
    output = args.output
    previous = json.loads(output.read_text()) if output.exists() else {}
    priority = json.loads((ROOT / "sources.json").read_text())
    expanded = json.loads((ROOT / "expanded-sources.json").read_text())
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    discovery = previous.get("registry_discovery", {})
    health = previous.get("sources", {})
    if not args.only and os.getenv("BRAVE_SEARCH_API_KEY") and due(discovery.get("checked_at"), now, 6 * national.HOUR):
        discovery["checked_at"] = now
        try:
            boards = discovery.setdefault("boards", {})
            for key, candidate in national.discover(discovery).items():
                if len(boards) < 500:
                    boards.setdefault(key, candidate)
            discovery["page"] = discovery.get("page", 0) + 1
            discovery["error"] = None
        except (OSError, KeyError, ValueError, TypeError, AttributeError) as error:
            discovery["error"] = f"HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__

    configured = [(provider, company, slug) for provider, boards in priority.items() for company, slug in boards.items()]
    secondary = [(provider, company, slug) for provider, boards in expanded.items() for company, slug in boards.items()]
    known = {(provider, slug.lower()) for provider, _, slug in configured + secondary}
    secondary.extend((board["provider"], f'{board["slug"]} ({board["provider"]})', board["slug"])
                     for board in discovery.get("boards", {}).values()
                     if (board["provider"], board["slug"].lower()) not in known)
    if args.only:
        sources = [source for source in configured + secondary if source[1] == args.only]
        if not sources:
            parser.error("No matching sources")
    elif args.scheduled:
        rotated = [source for source in secondary
                   if due(health.get(source[1], {}).get("checked_at"), now,
                          24 * national.HOUR if health.get(source[1], {}).get("error") else 2 * national.HOUR)]
        rotated.sort(key=lambda source: health.get(source[1], {}).get("checked_at", ""))
        sources = configured + rotated[:40]
    else:
        sources = configured + secondary

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(zip(sources, pool.map(lambda source: fetch(source, now), sources)))
    national_status = {}
    for company, provider, interval, enabled in national.feeds():
        national_status[company] = {"enabled": enabled, "interval_minutes": interval // 60}
        if enabled and not args.only and (not args.scheduled or due(health.get(company, {}).get("checked_at"), now, interval)):
            results.append(((provider, company, ""), collect_national(provider, now)))

    snapshot, successes = update(previous, results, now)
    disabled = {name for name, status in national_status.items() if not status["enabled"]}
    snapshot["jobs"] = [job for job in snapshot["jobs"] if job.get("source_key") not in disabled]
    snapshot["registry_discovery"] = discovery
    snapshot["coverage"] = {
        "configured": len(configured) + len(secondary),
        "employer_successes": sum(bool(snapshot["sources"].get(company, {}).get("succeeded_at"))
                                  for _, company, _ in configured + secondary),
        "national": national_status,
        "discovery_enabled": bool(os.getenv("BRAVE_SEARCH_API_KEY")),
    }
    for _, company, _ in configured + secondary:
        snapshot["sources"].setdefault(company, {})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, output)
    for (_, company, _), (jobs, count, error) in results:
        print(f"{company}: {error or f'{len(jobs)} matching of {count}'}")
    print(f"{successes}/{len(results)} sources succeeded")
    return 0 if successes else 1


if __name__ == "__main__":
    sys.exit(main())
