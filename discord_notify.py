import json
import os
import re
import urllib.request


def _send(webhook_url, payload):
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "internship-job-alerts/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status not in (200, 204):
            raise RuntimeError(
                f"Discord returned HTTP {response.status}"
            )


def send_internship_alert(
    company,
    title,
    location,
    url,
    matches,
    posted=None,
    javascript_exposure=None,
    profiles=None,
):
    webhook = os.environ["INTERNSHIP_DISCORD_WEBHOOK_URL"]
    # Matcher results and public callers use anonymous IDs. Private runtime data
    # may supply a friendly label or Discord mention for a specific recipient.
    labels = []
    mention_ids = []
    for profile_id in matches:
        if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", profile_id):
            raise ValueError("Matches must contain anonymous profile IDs")
        profile = (profiles or {}).get(profile_id, {})
        discord_id = profile.get("discord_user_id")
        if discord_id is not None and not re.fullmatch(r"\d{17,20}", discord_id):
            raise ValueError("Invalid private Discord user ID")
        if discord_id:
            labels.append(f"<@{discord_id}>")
            mention_ids.append(discord_id)
        else:
            labels.append(profile.get("display_name") or profile_id)

    fields = [
        {
            "name": "📍 Location",
            "value": location,
            "inline": True,
        },
        {
            "name": "🎯 Matches",
            "value": ", ".join(labels) or "None",
            "inline": True,
        },
    ]

    if posted:
        fields.append(
            {
                "name": "🕐 Posted",
                "value": posted,
                "inline": True,
            }
        )

    if javascript_exposure:
        fields.append(
            {
                "name": "JavaScript exposure",
                "value": javascript_exposure,
                "inline": True,
            }
        )

    payload = {
        "allowed_mentions": {"users": mention_ids},
        "embeds": [
            {
                "title": f"{company} — {title}",
                "url": url,
                "description": "🚨 **NEW INTERNSHIP FOUND**",
                "fields": fields,
            }
        ]
    }

    _send(webhook, payload)


def send_monitor_error(
    source,
    error,
    consecutive_failures,
    last_success=None,
):
    webhook = os.environ["MONITOR_ERROR_WEBHOOK_URL"]
    user_id = os.environ["DISCORD_USER_ID"]

    description = (
        f"<@{user_id}>\n\n"
        f"**Source:** {source}\n"
        f"**Consecutive failures:** {consecutive_failures}\n"
        f"**Error:** {error}"
    )

    if last_success:
        description += f"\n**Last successful check:** {last_success}"

    payload = {
        "content": f"<@{user_id}>",
        "allowed_mentions": {
            "users": [user_id]
        },
        "embeds": [
            {
                "title": "🚨 MONITOR ERROR",
                "description": description,
            }
        ],
    }

    _send(webhook, payload)


def send_recovery(source):
    webhook = os.environ["MONITOR_ERROR_WEBHOOK_URL"]

    payload = {
        "embeds": [
            {
                "title": "✅ MONITOR RECOVERED",
                "description": f"**{source}** is working again.",
            }
        ]
    }

    _send(webhook, payload)
