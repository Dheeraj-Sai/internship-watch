# Internship Watch

A Summer 2027 internship dashboard for a Texas A&M MS Computer Science search. A Python collector reads public employer boards. The browser filters the feed and stores your application records locally.

## Run locally

Requires Python 3.9 or newer. No dependencies to install.

```sh
python3 -B collect.py
python3 -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open [localhost:8000](http://127.0.0.1:8000). The included snapshot can be used without running the collector. Run `python3 -B collect.py --only Stripe` to check one employer.

## Deploy on GitHub Pages

1. Create a public repository and push this workspace to its `main` branch. The included public snapshot contains employer listing metadata.
2. In **Settings → Pages → Build and deployment**, select **GitHub Actions**. In **Settings → Actions**, allow repository workflows.
3. In **Actions → Refresh internship dashboard**, choose **Run workflow** on `main`. Open the deployment URL shown by the deploy job.
4. Verify that the **Sources** view shows successful checks and check the Actions tab after the next scheduled run. The schedule is `3-59/10 * * * *` in UTC. GitHub can delay or skip jobs and may disable the public repository’s schedule after 60 days without activity. Use **Run workflow** for a manual refresh.
5. If the **Save collector state** step fails, verify that the workflow’s write permission is allowed and that your branch rules permit the workflow to update `feed-state`. Reserve that branch for the workflow’s latest snapshot.

Standard GitHub-hosted runners and GitHub Pages are available at no charge for this public-repository setup. Opening or closing the dashboard does not affect collection. An open dashboard checks the deployed snapshot every two minutes.

## Update the watchlist

Edit `sources.json` to add or adjust a company’s verified Greenhouse, Lever or Ashby board identifier. Use **Sources** to see provider failures and pin employers you want to watch. Campus and other job boards are available as links. Save those listings with **Applications → Save private opening**.

- The **Summer 2027** tab includes internships with an unclear term by default. Use the term checkbox for a confirmed-term view.
- The collector uses role and location heuristics. The location override surfaces outside-U.S. and unrecognized locations. The full-time tab focuses on early-career CS roles.
- Employer-posted date depends on the feed. Greenhouse update time is separate. On first successful collection of a board, its existing jobs are treated as a baseline.
- CPT acceptance and sponsorship labels come from explicit phrases. Open the employer listing for full context. An unclear value means the collector could not determine a policy.
- Listings are closed after consecutive successful checks miss them. Individual source failures retain the previous results and show up in **Sources**.
- Application statuses, notes and watchlist selections are local to the browser. Export a JSON backup from **Applications** before clearing site data or switching devices.

## Add openings found on other job boards

Use `site/data/discovery.json` to attach discovery links to a specific employer-feed posting or add a manually reviewed direct employer listing. Job board links appear under the posting details and in the **Discovered on** filter. Manually reviewed links show **Check employer** and expire from the feed after 30 days unless reviewed again. The scheduled workflow checks employer feeds. Use board alerts, including HireAggies, for listings that require your sign-in and save them privately under **Applications**.
