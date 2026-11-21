"""Generate the dataset index and exports, or check the source and generated files."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date
import io
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parent
ACCESS = {"open", "free registration", "on request", "licensed", "paid", "unclear"}
HOST = "researchsnacks.semanticmachines.fyi"


def valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and not any(c.isspace() for c in value))
    except ValueError:
        return False


def validate(entries):
    errors, ids, urls = [], set(), set()
    if not isinstance(entries, list) or not entries:
        return ["datasets.yaml must contain a non-empty list"]
    for n, entry in enumerate(entries, 1):
        label = f"entry {n}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: expected a mapping")
            continue
        label = str(entry.get("id", label))
        for field in ("id", "title", "provider", "url", "access", "data"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"{label}: {field} must be a non-empty string")
        identifier = entry.get("id")
        if isinstance(identifier, str):
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
                errors.append(f"{label}: id must be a lowercase slug")
            if identifier in ids:
                errors.append(f"{label}: duplicate id")
            ids.add(identifier)
        url = entry.get("url")
        if not valid_url(url):
            errors.append(f"{label}: invalid provider URL")
        elif url.rstrip("/") in urls:
            errors.append(f"{label}: duplicate provider URL")
        else:
            urls.add(url.rstrip("/"))
        if not isinstance(entry.get("access"), str) or entry["access"] not in ACCESS:
            errors.append(f"{label}: access must be one of {', '.join(sorted(ACCESS))}")
        licence = entry.get("licence")
        if (not isinstance(licence, dict) or not isinstance(licence.get("name"), str)
                or not licence["name"].strip() or not valid_url(licence.get("url"))):
            errors.append(f"{label}: licence needs a name and URL")
        if "citation" not in entry or (entry["citation"] is not None and not valid_url(entry["citation"])):
            errors.append(f"{label}: citation must be a URL or null")
        snacks = entry.get("snacks")
        if not isinstance(snacks, list):
            errors.append(f"{label}: snacks must be a list (empty if unused)")
        else:
            for snack in snacks:
                if (not isinstance(snack, dict) or not isinstance(snack.get("title"), str)
                        or not snack["title"].strip() or not valid_url(snack.get("url"))
                        or urlsplit(snack["url"]).hostname != HOST):
                    errors.append(f"{label}: each snack needs a title and a URL on {HOST}")
        found = entry.get("found")
        if not isinstance(found, dict) or not isinstance(found.get("context"), str) or not found["context"].strip():
            errors.append(f"{label}: found needs a date and context")
        for field, value in (("checked", entry.get("checked")),
                             ("found.date", found.get("date") if isinstance(found, dict) else None)):
            try:
                parsed = date.fromisoformat(str(value))
                if parsed > date.today():
                    raise ValueError("future date")
            except (TypeError, ValueError):
                errors.append(f"{label}: {field} must be an ISO date no later than today")
        if "notes" in entry and not isinstance(entry["notes"], str):
            errors.append(f"{label}: notes must be text")
    return errors


def collect_urls(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from collect_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_urls(item)
    elif valid_url(value):
        yield value


def check_link(url):
    try:
        request = Request(url, headers={"User-Agent": "research-datasets/0.1 (catalogue link check)"})
        with urlopen(request, timeout=25) as response:
            status = response.status
        if 200 <= status < 400:
            return None
        return f"HTTP {status}: {url}"
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        return f"{url}: {error}"


def cell(text):
    return str(text).replace("|", "&#124;").replace("\n", " ").strip()


def outputs(entries):
    entries = sorted(entries, key=lambda entry: entry["title"].casefold())
    lines = ["---", "title: Research datasets", "licence: CC BY 4.0",
             "provenance: datasets.yaml; generated by build.py", "---", "",
             "# Research datasets", "",
             "Public datasets encountered while checking claims for [Research Snacks](https://researchsnacks.semanticmachines.fyi/), with a brief description of what each holds and links to the snacks that used it. The catalogue also includes datasets considered for a check but not used.", "",
             "**Access** says how you get the data: *open* (download or browse without an account), *free registration*, *on request* (ask the provider), *licensed* (accept a user licence or use a licensed service, sometimes free), *paid*, or *unclear* (the provider pages could not be verified). Reuse terms differ from access: an open page can still restrict copying. Each entry's licence and notes are in [datasets.yaml](datasets.yaml) and [datasets.csv](datasets.csv).", "",
             "<!-- Generated from datasets.yaml. Edit the YAML and run uv run build.py. -->", "",
             "| Dataset | What's in it | Access | Used in |",
             "|---|---|---|---|"]
    for entry in entries:
        used = "; ".join(f"[{cell(s['title'])}]({s['url']})" for s in entry["snacks"]) or "—"
        lines.append(f"| [{cell(entry['title'])}]({entry['url']}) | {cell(entry['data'])} | {entry['access']} | {used} |")
    lines += ["", "## Adding a dataset", "",
              "Edit [datasets.yaml](datasets.yaml) to add the provider's landing page, access and licence terms, two to four sentences describing the data, citation guidance where available, and any snack that used it. Leave `snacks: []` for datasets considered but not used. Record where and when you found it, check the provider and terms pages, and update `checked` with the date. If a page cannot be verified, explain the limitation in `notes`; the date then records the attempted check.", "",
              "Run `uv run build.py` to update this index and the [CSV](datasets.csv) and [JSON](datasets.json) exports. Run `uv run build.py --check` to validate the entries and confirm the generated files match. Add `--links` to check every linked URL for an HTTP 2xx or 3xx response. Commit the YAML and generated files together.", "",
              "Catalogue text is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); datasets remain under their providers' own terms. See [LICENCE.md](LICENCE.md).", ""]
    buf = io.StringIO(newline="")
    fields = ["id", "title", "provider", "url", "access", "licence_name", "licence_url", "data", "citation", "snacks", "found_date", "found_context", "checked", "notes"]
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for entry in entries:
        writer.writerow({**{key: entry.get(key, "") for key in ("id", "title", "provider", "url", "access", "data", "citation", "checked", "notes")},
                         "licence_name": entry["licence"]["name"], "licence_url": entry["licence"]["url"],
                         "snacks": json.dumps(entry["snacks"], ensure_ascii=False),
                         "found_date": entry["found"]["date"], "found_context": entry["found"]["context"]})
    return {"README.md": "\n".join(lines), "datasets.json": json.dumps(entries, ensure_ascii=False, indent=2) + "\n", "datasets.csv": buf.getvalue()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate without writing; require up-to-date exports")
    parser.add_argument("--links", action="store_true", help="also check every linked URL over the network")
    args = parser.parse_args()
    try:
        entries = yaml.safe_load((ROOT / "datasets.yaml").read_text())
    except (OSError, yaml.YAMLError) as error:
        raise SystemExit(f"FAIL: {error}")
    errors = validate(entries)
    if errors:
        raise SystemExit("FAIL:\n" + "\n".join(errors))
    # Normalise YAML dates once for deterministic JSON and CSV exports.
    for entry in entries:
        entry["checked"] = str(entry["checked"])
        entry["found"]["date"] = str(entry["found"]["date"])
    generated = outputs(entries)
    for name, content in generated.items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_bytes() != content.encode("utf-8"):
                errors.append(f"{name} is missing or stale; run uv run build.py")
        else:
            path.write_text(content, encoding="utf-8")
    if args.links:
        urls = sorted(set(collect_urls(entries)))
        with ThreadPoolExecutor(max_workers=8) as pool:
            errors.extend(error for error in pool.map(check_link, urls) if error)
        print(f"Checked {len(urls)} unique URLs.")
    if errors:
        raise SystemExit("FAIL:\n" + "\n".join(errors))
    print(f"PASS: {len(entries)} datasets; schema valid; generated files {'match' if args.check else 'written'}.")


if __name__ == "__main__":
    main()
