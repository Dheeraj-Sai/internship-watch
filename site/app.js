"use strict";

const $ = (selector) => document.querySelector(selector);
const stages = ["Saved", "Applied", "Assessment", "Interview", "Offer", "Rejected", "Withdrawn"];
const statuses = ["Explicitly supported", "Restricted", "Unclear"];
const discoveryFile = fetch("data/discovery.json").then((response) => response.ok ? response.json() : null).catch(() => null);
const day = 86400000;
const storageKey = "internship-watch-v1";
const dateFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  timeZoneName: "short",
});
const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
let state = { applications: [], watch: [], reviewed: null };
let snapshot = { jobs: [], sources: {} };
let loadError = false;
let view = "feed";
let tab = "summer";
let limit = 30;

try {
  const saved = JSON.parse(localStorage.getItem(storageKey));
  if (saved) state = validateBackup(saved);
} catch {
  $("#tracker-status").textContent = "Browser data could not be loaded.";
}

function persist() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(state));
    return true;
  } catch {
    $("#tracker-status").textContent = "Browser storage is unavailable. Export to keep this session’s changes.";
    return false;
  }
}

function element(tag, label, className) {
  const node = document.createElement(tag);
  if (label) node.textContent = label;
  if (className) node.className = className;
  return node;
}

function button(label, handler) {
  const node = element("button", label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function url(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.href : "";
  } catch {
    return "";
  }
}

function link(label, address, className) {
  const href = url(address);
  if (!href) return element("span", "Link unavailable");
  const node = element("a", label, className);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}

function date(value) {
  return value && Number.isFinite(Date.parse(value)) ? dateFormat.format(new Date(value)) : "Unknown";
}

function ago(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return "Unknown";
  const minutes = Math.round((Date.parse(value) - Date.now()) / 60000);
  if (Math.abs(minutes) < 60) return relative.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  return Math.abs(hours) < 24 ? relative.format(hours, "hour") : relative.format(Math.round(hours / 24), "day");
}

function setOptions(select, first, values) {
  const current = select.value;
  select.replaceChildren(new Option(first, ""), ...values.map((value) => new Option(value, value)));
  if (values.includes(current)) select.value = current;
}

function watch(company) {
  state.watch = state.watch.includes(company)
    ? state.watch.filter((item) => item !== company) : [...state.watch, company];
  persist();
  render();
}

function watchButton(company) {
  const active = state.watch.includes(company);
  const node = button(active ? "Watching" : "Watch company", () => watch(company));
  node.setAttribute("aria-pressed", String(active));
  node.setAttribute("aria-label", active ? `Stop watching ${company}` : `Watch ${company}`);
  return node;
}

function saveJob(job) {
  if (state.applications.some((item) => item.id === job.id)) return;
  state.applications.unshift({
    id: job.id, title: job.title, company: job.company, url: job.url,
    stage: "Saved", notes: "", applied: "", followUp: "",
  });
  persist();
  render();
}

function addLine(parent, label, value) {
  if (!value) return;
  const line = element("p");
  line.append(element("strong", `${label} · `), document.createTextNode(value));
  parent.append(line);
}

function deadlineDate(value) {
  if (!value) return Infinity;
  const explicit = value.match(/\b(?:[A-Za-z]+\s+\d{1,2},?\s+20\d{2}|20\d{2}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/20\d{2})\b/);
  return explicit && Number.isFinite(Date.parse(explicit[0])) ? Date.parse(explicit[0]) : Infinity;
}

function jobCard(job) {
  const card = element("article", "", "card");
  const head = element("div", "", "card-head");
  const title = element("div");
  title.append(element("h3", job.title), element("p", [job.company, job.locations.join(" / ")].filter(Boolean).join(" · "), "meta"));
  const actions = element("div", "", "actions");
  const saved = state.applications.some((item) => item.id === job.id);
  actions.append(button(saved ? "View saved" : "Save", () => saved ? switchView("tracker") : saveJob(job)),
    link(job.provider === "direct" ? "Check employer" : "Apply", job.url, "primary"));
  head.append(title, actions);
  const tags = element("div", "", "tags");
  const labels = [job.kind, job.kind !== "Full-time" ? job.term === "Unclear" ? "Term unclear" : job.term : "", ...job.tracks,
    job.phd_only ? "PhD listed in title" : job.degree ? "Graduate degree mentioned" : "",
    job.cpt.status === "Unclear" ? "" : `CPT: ${job.cpt.status}`,
    job.sponsorship.status === "Unclear" ? "" : `Sponsorship: ${job.sponsorship.status}`,
    job.status === "Closed" ? "Closed" : "",
    job.provider === "direct" ? "Manually reviewed" : "",
    ...((job.discovered || []).map((source) => `Found on ${source.label}`))];
  for (const label of labels.filter(Boolean)) tags.append(element("span", label, "tag"));
  const timing = element("p", [
    job.posted_at ? `Posted ${ago(job.posted_at)}` : "Posted date unavailable",
    `First seen ${ago(job.first_seen_at)}`,
    job.reopened_at ? `Reopened ${ago(job.reopened_at)}` : "",
  ].filter(Boolean).join(" · "), "meta");
  const details = element("details");
  details.append(element("summary", "Posting details and eligibility"));
  addLine(details, "CPT policy", [job.cpt.status, job.cpt.quote].filter(Boolean).join(" — "));
  addLine(details, "Future sponsorship", [job.sponsorship.status, job.sponsorship.quote].filter(Boolean).join(" — "));
  addLine(details, "Citizenship or clearance language", job.clearance);
  addLine(details, "Graduation timing", job.graduation);
  addLine(details, "Deadline language", job.deadline);
  addLine(details, "Pay", job.pay);
  addLine(details, "Workplace", job.workplace);
  addLine(details, job.provider === "direct" ? "Manually reviewed" : "Last verified",
    job.provider === "direct" ? date(job.last_seen_at) : `${date(job.last_seen_at)} via ${job.provider}`);
  if (job.discovered?.length) {
    const references = element("p");
    references.append(document.createTextNode("Discovered on · "));
    job.discovered.forEach((source, index) => {
      if (index) references.append(document.createTextNode(" / "));
      references.append(link(source.label, source.url));
    });
    details.append(references);
  }
  details.append(watchButton(job.company));
  card.append(head, tags, timing, details);
  return card;
}

function fit(job) {
  return Number(job.term === "Summer 2027") * 4 + Number(Boolean(job.degree)) * 2
    + Number(job.cpt.status === "Explicitly supported") * 2
    - Number(job.cpt.status === "Restricted") * 3 - Number(job.phd_only) * 4;
}

function renderFeed() {
  const form = $("#filters");
  const filters = new FormData(form);
  const chosen = (name) => filters.get(name) || "";
  const basis = chosen("basis");
  const recent = (job) => Math.max(Date.parse(job.first_seen_at) || 0, Date.parse(job.reopened_at) || 0);
  const rows = snapshot.jobs.filter((job) => {
    if (!chosen("closed") && job.status === "Closed") return false;
    if (!chosen("nonus") && !job.us) return false;
    if (tab === "summer" && (job.kind !== "Internship" ||
      (job.term !== "Summer 2027" && !(chosen("unclear") && job.term === "Unclear")))) return false;
    if ({ intern: "Internship", coop: "Co-op", full: "Full-time" }[tab] &&
      job.kind !== { intern: "Internship", coop: "Co-op", full: "Full-time" }[tab]) return false;
    if (chosen("unseen") && ((job.baseline && !job.reopened_at) || recent(job) <= (Date.parse(state.reviewed) || 0))) return false;
    if (chosen("watch") && !state.watch.includes(job.company)) return false;
    if (chosen("deadline") && !job.deadline) return false;
    if (chosen("age") && !(Date.parse(job[basis]) >= Date.now() - Number(chosen("age")) * 3600000)) return false;
    const text = [job.title, job.company, ...job.locations].join(" ").toLowerCase();
    if (!chosen("search").toLowerCase().trim().split(/\s+/).every((word) => text.includes(word))) return false;
    if (!job.locations.join(" ").toLowerCase().includes(chosen("location").trim().toLowerCase())) return false;
    if (chosen("track") && !job.tracks.includes(chosen("track"))) return false;
    if (chosen("company") && chosen("company") !== job.company) return false;
    if (chosen("provider") && chosen("provider") !== job.provider) return false;
    if (chosen("discovery") && !job.discovered?.some((source) => source.label === chosen("discovery"))) return false;
    if (chosen("workplace") && chosen("workplace").toLowerCase() !== job.workplace.toLowerCase().replace("on site", "on-site")) return false;
    if (chosen("degree") === "masters" && (!job.degree || job.phd_only)) return false;
    if (chosen("degree") === "graduation" && !job.graduation) return false;
    return ["cpt", "sponsorship"].every((key) => !chosen(key) || chosen(key) === job[key].status);
  });
  const sort = $("#sort").value;
  rows.sort((a, b) => {
    if (sort === "deadline") {
      const today = Date.parse(new Date().toISOString().slice(0, 10));
      const upcoming = (job) => deadlineDate(job.deadline) >= today ? deadlineDate(job.deadline) : Infinity;
      if (upcoming(a) !== upcoming(b)) return upcoming(a) - upcoming(b);
    }
    if (sort === "fit" && fit(a) !== fit(b)) return fit(b) - fit(a);
    return (Date.parse(b[basis]) || 0) - (Date.parse(a[basis]) || 0) || recent(b) - recent(a);
  });
  $("#count").textContent = `${rows.length} ${rows.length === 1 ? "opening" : "openings"}`;
  $("#jobs").replaceChildren(...rows.slice(0, limit).map(jobCard));
  if (!rows.length) $("#jobs").append(element("p", "No matching openings. Try broadening your filters.", "panel"));
  $("#more").hidden = rows.length <= limit;
}

function recordField(label, input, item, key) {
  const wrap = element("label", label);
  input.value = item[key];
  input.addEventListener("change", () => {
    item[key] = input.value;
    if (key === "stage" && input.value === "Applied" && !item.applied) {
      item.applied = new Date().toLocaleDateString("sv-SE", { timeZone: "America/Chicago" });
      persist();
      renderTracker();
    } else {
      persist();
    }
  });
  wrap.append(input);
  return wrap;
}

function renderTracker() {
  const cards = state.applications.map((item) => {
    const card = element("article", "", "card");
    const header = element("div", "", "toolbar");
    const title = element("div");
    title.append(element("h3", item.title), element("p", item.company, "meta"));
    const actions = element("div", "", "actions");
    actions.append(link("Open listing", item.url), button("Remove", () => {
      if (!window.confirm(`Remove ${item.title} from your tracker?`)) return;
      state.applications = state.applications.filter((row) => row.id !== item.id);
      persist();
      renderTracker();
    }));
    header.append(title, actions);
    const fields = element("div", "", "record");
    const stage = element("select");
    stage.append(...stages.map((entry) => new Option(entry)));
    const applied = element("input");
    applied.type = "date";
    const followUp = element("input");
    followUp.type = "date";
    const notes = element("textarea");
    notes.rows = 2;
    notes.maxLength = 4000;
    fields.append(recordField("Status", stage, item, "stage"), recordField("Applied on", applied, item, "applied"),
      recordField("Follow up", followUp, item, "followUp"), recordField("Notes", notes, item, "notes"));
    card.append(header, fields);
    return card;
  });
  $("#applications").replaceChildren(...cards);
  if (!cards.length) $("#applications").append(element("p", "Save a role from the feed or add a private opening above.", "panel"));
}

function renderCoverage() {
  const cards = Object.entries(snapshot.sources).sort(([a], [b]) => a.localeCompare(b)).map(([company, health]) => {
    const card = element("article", "", "source-row");
    const old = !health.succeeded_at || Date.now() - Date.parse(health.succeeded_at) > 25 * 60000;
    card.append(element("h3", company), element("p",
      health.error ? `Check failed · ${health.error}` : old ? "Delayed" : `Healthy · ${health.matching} matching`, "meta"),
    element("p", `Last success · ${date(health.succeeded_at)}`, "meta"), watchButton(company));
    return card;
  });
  $("#sources").replaceChildren(...cards);
}

function renderFreshness() {
  const freshness = $("#freshness");
  const last = snapshot.last_success_at;
  const stale = !last || Date.now() - Date.parse(last) > 25 * 60000;
  freshness.dataset.stale = String(stale || loadError);
  freshness.textContent = last
    ? `Feed ${stale ? "delayed" : "updated"} · ${date(last)}${loadError ? " · Connection interrupted" : ""}`
    : loadError ? "Feed unavailable. Retrying automatically." : "Waiting for the first successful collection.";
}

function render() {
  renderFreshness();
  if (view === "feed") renderFeed();
  if (view === "tracker") renderTracker();
  if (view === "coverage") renderCoverage();
}

function switchView(next) {
  view = next;
  for (const name of ["feed", "tracker", "coverage"]) {
    $(`#${name}`).hidden = name !== view;
    $(`[data-view="${name}"]`).setAttribute("aria-pressed", String(name === view));
  }
  $("#heading").textContent = { feed: "Summer 2027 internships", tracker: "Application tracker", coverage: "Source coverage" }[view];
  $("#subtitle").hidden = view !== "feed";
  render();
}

function validateBackup(data) {
  if (!data || !Array.isArray(data.applications) || !Array.isArray(data.watch)) throw new Error("Unrecognized tracker backup");
  const clean = [];
  const ids = new Set();
  for (const item of data.applications) {
    if (!item || typeof item.id !== "string" || typeof item.title !== "string" || typeof item.company !== "string"
      || !url(item.url) || !stages.includes(item.stage) || ids.has(item.id)) throw new Error("Invalid application record");
    ids.add(item.id);
    const validDate = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
    clean.push({ id: item.id, url: url(item.url), title: item.title.slice(0, 200), company: item.company.slice(0, 100),
      stage: item.stage, notes: typeof item.notes === "string" ? item.notes.slice(0, 4000) : "",
      applied: validDate(item.applied), followUp: validDate(item.followUp) });
  }
  return { applications: clean, watch: [...new Set(data.watch.filter((name) => typeof name === "string"))],
    reviewed: Number.isFinite(Date.parse(data.reviewed)) ? data.reviewed : null };
}

async function refresh() {
  try {
    const response = await fetch("data/jobs.json", { cache: "no-cache" });
    if (!response.ok) throw new Error("Feed unavailable");
    const incoming = await response.json();
    if (!Array.isArray(incoming.jobs) || !incoming.sources) throw new Error("Invalid feed");
    const changed = snapshot.checked_at !== incoming.checked_at;
    const discovery = await discoveryFile;
    if (discovery) {
      for (const job of incoming.jobs) {
        const pathname = new URL(job.url).pathname;
        const references = discovery.matches.find((entry) =>
          entry.company === job.company && pathname.split("/").includes(entry.postingId));
        if (references) job.discovered = references.sources;
      }
      const unknown = { status: "Unclear", quote: "" };
      for (const entry of discovery.direct) {
        if (Date.now() - Date.parse(entry.reviewed) >= 30 * day) continue;
        incoming.jobs.push({
          ...entry, kind: "Internship", term: "Summer 2027", tracks: ["Software engineering"],
          us: true, status: "Open", provider: "direct", baseline: true,
          posted_at: null, first_seen_at: entry.reviewed, last_seen_at: entry.reviewed,
          cpt: unknown, sponsorship: entry.sponsorship || unknown,
        });
      }
    }
    snapshot = incoming;
    if (changed) {
      setOptions($("#filters [name=track]"), "All families", [...new Set(snapshot.jobs.flatMap((job) => job.tracks))].sort());
      setOptions($("#filters [name=company]"), "All companies", [...new Set([...Object.keys(snapshot.sources), ...snapshot.jobs.map((job) => job.company)])].sort());
      setOptions($("#filters [name=discovery]"), "Any discovery source",
        [...new Set(snapshot.jobs.flatMap((job) => (job.discovered || []).map((source) => source.label)))].sort());
    }
    loadError = false;
    renderFreshness();
    if (changed && view !== "tracker") {
      $("#reviewed").textContent = "Mark feed reviewed";
      render();
    }
  } catch {
    loadError = true;
    renderFreshness();
  }
}

for (const key of ["cpt", "sponsorship"]) setOptions($(`[name=${key}]`), "Any status", statuses);
document.querySelectorAll("[data-view]").forEach((node) => node.addEventListener("click", () => switchView(node.dataset.view)));
document.querySelectorAll("[data-tab]").forEach((node) => node.addEventListener("click", () => {
  tab = node.dataset.tab;
  limit = 30;
  document.querySelectorAll("[data-tab]").forEach((item) => item.setAttribute("aria-pressed", String(item === node)));
  renderFeed();
}));
$("#filters").addEventListener("input", () => { limit = 30; renderFeed(); });
$("#filters").addEventListener("reset", () => setTimeout(() => { limit = 30; renderFeed(); }, 0));
$("#filters").addEventListener("submit", (event) => event.preventDefault());
$("#sort").addEventListener("change", renderFeed);
$("#more").addEventListener("click", () => { limit += 30; renderFeed(); });
$("#reviewed").addEventListener("click", () => {
  if (!snapshot.last_success_at) return;
  state.reviewed = snapshot.checked_at;
  persist();
  renderFeed();
  $("#reviewed").textContent = "Feed marked reviewed";
});
$("#manual").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const input = new FormData(form);
  const address = url(input.get("url"));
  const title = input.get("title").trim();
  const company = input.get("company").trim();
  if (!address || !title || !company) {
    $("#tracker-status").textContent = "Enter a role, company, and valid HTTPS job link.";
    return;
  }
  saveJob({ id: `private:${address}`, title, company, url: address });
  form.reset();
});
$("#export").addEventListener("click", () => {
  const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }));
  const anchor = element("a");
  anchor.href = objectUrl;
  anchor.download = "internship-watch-backup.json";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
});
$("#import").addEventListener("change", async (event) => {
  const input = event.currentTarget;
  try {
    const file = input.files[0];
    if (!file || file.size > 5_000_000) throw new Error("Choose a tracker backup under 5 MB");
    const backup = validateBackup(JSON.parse(await file.text()));
    const records = new Map(backup.applications.map((item) => [item.id, item]));
    for (const item of state.applications) records.set(item.id, item);
    state.applications = [...records.values()];
    state.watch = [...new Set([...state.watch, ...backup.watch])];
    state.reviewed = [state.reviewed, backup.reviewed].filter(Boolean).sort().pop() || null;
    if (persist()) $("#tracker-status").textContent = "Backup merged with this browser’s tracker.";
    renderTracker();
  } catch (error) {
    $("#tracker-status").textContent = error.message;
  } finally {
    input.value = "";
  }
});
render();
refresh();
setInterval(refresh, 120000);
