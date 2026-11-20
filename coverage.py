"""Compare a dated, manually verified sample of employer links against the feed."""
import argparse
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def posting_key(raw):
    url = urlsplit(raw)
    greenhouse_id = parse_qs(url.query).get("gh_jid")
    if greenhouse_id:
        return "greenhouse", greenhouse_id[0]
    path = url.path.rstrip("/")
    if url.hostname in ("boards.greenhouse.io", "job-boards.greenhouse.io") and "/jobs/" in path:
        return "greenhouse", path.rsplit("/", 1)[1]
    if url.hostname in ("jobs.ashbyhq.com", "jobs.lever.co"):
        path = path.removesuffix("/application").removesuffix("/apply")
    employer_id = re.match(r"/jobs/(\d+)(?:/|$)", path)
    if employer_id and url.hostname in ("www.pinterestcareers.com", "pinterestcareers.com"):
        return "greenhouse", employer_id[1]
    return (url.hostname or "").removeprefix("www."), path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path, help="JSON array with source and employer_url keys for each verified open role")
    parser.add_argument("--feed", type=Path, default=Path("site/data/jobs.json"))
    parser.add_argument("--discovery", type=Path, default=Path("site/data/discovery.json"))
    args = parser.parse_args()
    sample = json.loads(args.sample.read_text())
    feed = json.loads(args.feed.read_text())
    discovery = json.loads(args.discovery.read_text())
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    covered = {posting_key(job["url"]) for job in feed["jobs"] if job["status"] == "Open"}
    covered.update(posting_key(job["url"]) for job in discovery["direct"]
                   if dt.datetime.fromisoformat(job["reviewed"]) >= cutoff)
    by_source = {}
    for item in sample:
        by_source.setdefault(item["source"], set()).add(posting_key(item["employer_url"]))
    unique = set().union(*by_source.values())
    print(f"Coverage of supplied sample: {len(unique & covered)}/{len(unique)}")
    for source, keys in sorted(by_source.items()):
        print(f"{source}: {len(keys & covered)}/{len(keys)}")
    for key in sorted(unique - covered):
        print("Missing:", *key)


if __name__ == "__main__":
    main()
