import json
import ssl
import urllib.parse
import urllib.request

import certifi


USER_AGENT = "internship-job-alerts/1.0"

SSL_CONTEXT = ssl.create_default_context(
    cafile=certifi.where()
)


def _with_content(endpoint):
    parsed = urllib.parse.urlsplit(endpoint)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query["content"] = "true"

    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def fetch_greenhouse_jobs(company):
    """
    Fetch and normalize all currently published jobs
    from one Greenhouse employer board.
    """

    if company.get("provider") != "greenhouse":
        raise ValueError(
            f"{company.get('employer')} is not a Greenhouse source"
        )

    endpoint = company.get("endpoint")

    if not endpoint:
        raise ValueError(
            f"{company.get('employer')} has no Greenhouse endpoint"
        )

    url = _with_content(endpoint)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
        context=SSL_CONTEXT,
    ) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Greenhouse returned HTTP {response.status}"
            )

        data = json.load(response)

    jobs = []

    for job in data.get("jobs", []):
        location = job.get("location") or {}

        jobs.append(
            {
                "source": "greenhouse",
                "employer": company["employer"],
                "canonical_employer": company.get(
                    "canonical_employer",
                    company["employer"],
                ),
                "job_id": str(job.get("id")),
                "title": job.get("title", ""),
                "location": location.get("name", ""),
                "url": job.get("absolute_url", ""),
                "updated_at": job.get("updated_at"),
                "description_html": job.get("content", ""),
            }
        )

    return jobs
