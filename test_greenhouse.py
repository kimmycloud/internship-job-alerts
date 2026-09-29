import json

from sources.greenhouse import fetch_greenhouse_jobs


with open("companies.json", "r", encoding="utf-8") as f:
    registry = json.load(f)


greenhouse_companies = [
    company
    for company in registry["companies"]
    if company.get("provider") == "greenhouse"
    and company.get("monitoring_ready") is True
]


print(
    f"Found {len(greenhouse_companies)} "
    f"monitoring-ready Greenhouse sources."
)


for company in greenhouse_companies:
    try:
        jobs = fetch_greenhouse_jobs(company)

        print(
            f"SUCCEEDED: {company['employer']}: "
            f"{len(jobs)} jobs"
        )

    except Exception as exc:
        print(
            f"FAILED: {company['employer']}: {exc}"
        )
