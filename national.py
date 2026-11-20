"""Optional national feeds and discovery using documented search APIs."""
import json
import os
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "careers.smartrecruiters.com": "smartrecruiters",
}
HOUR = 3600


def get_json(url, headers=None):
    request = Request(url, headers={"User-Agent": "InternshipWatch/1.0", "Accept": "application/json", **(headers or {})})
    with urlopen(request, timeout=12) as response:
        return json.load(response)


def search(url, params, headers=None):
    return get_json(f"{url}?{urlencode(params)}", headers)


def feeds():
    return [
        ("Adzuna", "adzuna", 6 * HOUR, all(os.getenv(key) for key in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"))),
        ("USAJOBS", "usajobs", HOUR, all(os.getenv(key) for key in ("USAJOBS_API_KEY", "USAJOBS_USER_AGENT"))),
    ]


def national_rows(provider):
    if provider == "adzuna":
        for keyword in ("intern", "co-op"):
            payload = search("https://api.adzuna.com/v1/api/jobs/us/search/1", {
                "app_id": os.environ["ADZUNA_APP_ID"], "app_key": os.environ["ADZUNA_APP_KEY"],
                "results_per_page": 50, "sort_by": "date", "what": keyword, "max_days_old": 30,
            })
            for item in payload["results"]:
                name = (item.get("company") or {}).get("display_name") or "Employer not stated"
                yield name, {
                    "title": item["title"], "description": item.get("description"),
                    "location": (item.get("location") or {}).get("display_name", ""),
                    "jobUrl": item["redirect_url"], "publishedAt": item.get("created"),
                    "requisition_id": "adzuna:" + str(item["id"]),
                    "address": {"postalAddress": {"addressCountry": "USA"}},
                }
    else:
        payload = search("https://data.usajobs.gov/api/search", {"HiringPath": "student", "ResultsPerPage": 100}, {
            "Authorization-Key": os.environ["USAJOBS_API_KEY"],
            "User-Agent": os.environ["USAJOBS_USER_AGENT"],
        })
        for result in payload["SearchResult"]["SearchResultItems"]:
            item = result["MatchedObjectDescriptor"]
            positions = item.get("PositionLocation") or []
            yield item["OrganizationName"], {
                "title": item["PositionTitle"], "description": item.get("QualificationSummary"),
                "employmentType": "Internship" if re.search(r"student trainee|pathways", item["PositionTitle"], re.I) else "",
                "secondaryLocations": [{"location": place.get("LocationName", "")} for place in positions],
                "address": {"postalAddress": {"addressCountry": "USA" if any(place.get("CountryCode") in ("US", "USA") for place in positions) else ""}},
                "jobUrl": item["PositionURI"], "publishedAt": item.get("PublicationStartDate"),
                "requisition_id": "usajobs:" + str(result["MatchedObjectId"]),
                "deadline": item.get("ApplicationCloseDate"),
            }


def discover(previous):
    """Find public board URLs without crawling or storing job-board search pages."""
    if not os.getenv("BRAVE_SEARCH_API_KEY"):
        return {}
    providers = list(BOARD_HOSTS)
    page = previous.get("page", 0)
    host = providers[page % len(providers)]
    payload = search("https://api.search.brave.com/res/v1/web/search", {
        "q": f'site:{host} ("summer 2027" OR "internship")',
        "country": "US", "count": 20, "offset": (page // len(providers)) % 5,
    }, {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]})
    boards = {}
    for item in payload.get("web", {}).get("results", []):
        path = urlsplit(item.get("url", ""))
        parts = path.path.strip("/").split("/")
        if path.scheme != "https" or path.hostname != host:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", parts[0]):
            continue
        provider = BOARD_HOSTS[host]
        slug = parts[0]
        boards[f"{provider}:{slug.lower()}"] = {"provider": provider, "slug": slug}
    return boards
