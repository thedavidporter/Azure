import logging
import sys
from datetime import datetime, timedelta, timezone

import requests
from azure.identity import DefaultAzureCredential
from azure.mgmt.datafactory import DataFactoryManagementClient
from azure.mgmt.datafactory.models import (
    RunFilterParameters,
    RunQueryFilter,
    RunQueryFilterOperand,
    RunQueryFilterOperator,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TENANT_ID       = "2199bfba-a409-4f13-b0c4-18b45933d88d"
SUBSCRIPTION_ID = "57493fde-eff8-432f-8574-4f1281bd2ce3"

PUSHOVER_USER_KEY  = "uiyzi5kbuxuf39cbr31xxg7d9864kt"
PUSHOVER_APP_TOKEN = "a3j121wgohd9xqvjomi322r3qygr6p"

TEAMS_WEBHOOK_URL = (
    "https://prod-03.usgovtexas.logic.azure.us:443/workflows/"
    "6f677bd210804d2b81f2fe3593bd2b2e/triggers/manual/paths/invoke"
    "?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0"
    "&sig=u31KuhlOzX6FWwsPnX7tx7pdPU430Bi-dnrxb04MHRs"
)

# Azure Government ADF portal base URL — change to adf.azure.com for commercial
ADF_PORTAL = "https://adf.azure.us"

ENVIRONMENTS = {
    "dev": {
        "resource_group": "zus1-idoh-dev-v2-rg",
        "factory_name":   "zus1-idoh-dev-v2-df",
    },
    "prd": {
        "resource_group": "zus1-idoh-prd-v1-rg",
        "factory_name":   "zus1-idoh-prd-v1-df",
    },
}

LOG_FILE = "adf_pipeline_failures.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_lookback_hours() -> int:
    """Return 72 on Monday (covers the weekend), 24 every other weekday."""
    return 72 if datetime.now().weekday() == 0 else 24


def monitor_url(run_id: str, resource_group: str, factory_name: str) -> str:
    """Deep link directly to a specific pipeline run in ADF Monitor.
    No user ID is embedded — links to the run by subscription/factory/runId only."""
    factory_path = (
        f"/subscriptions/{SUBSCRIPTION_ID}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.DataFactory/factories/{factory_name}"
    )
    return f"{ADF_PORTAL}/en/monitoring/pipelineruns/{run_id}?factory={factory_path}"


def query_failed_runs(
    client: DataFactoryManagementClient,
    resource_group: str,
    factory_name: str,
    start_time: datetime,
    end_time: datetime,
) -> list:
    filter_params = RunFilterParameters(
        last_updated_after=start_time,
        last_updated_before=end_time,
        filters=[
            RunQueryFilter(
                operand=RunQueryFilterOperand.STATUS,
                operator=RunQueryFilterOperator.EQUALS,
                values=["Failed"],
            )
        ],
    )
    result = client.pipeline_runs.query_by_factory(
        resource_group_name=resource_group,
        factory_name=factory_name,
        filter_parameters=filter_params,
    )
    return result.value or []


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_teams(failures: list, lookback_hours: int, generated_at: str) -> None:
    """Adaptive Card matching the plain-text format but with each pipeline name as a clickable link."""
    total = len(failures)
    body_blocks = [
        {
            "type": "TextBlock",
            "text": f"ADF Failures ({lookback_hours}h)",
            "weight": "Bolder",
            "size": "Medium",
        },
    ]

    # Group by environment
    by_env: dict[str, list] = {}
    for f in failures:
        by_env.setdefault(f["env"], []).append(f)

    for env, runs in by_env.items():
        body_blocks.append({
            "type": "TextBlock",
            "text": f"**{env}: {len(runs)} failure(s)**",
            "weight": "Bolder",
            "spacing": "Medium",
        })
        # All pipeline names for this env as a single TextBlock — one linked name per line
        lines = "\n".join(f"[{run['pipeline_name']}]({run['url']})" for run in runs)
        body_blocks.append({
            "type": "TextBlock",
            "text": lines,
            "wrap": True,
            "spacing": "Small",
        })

    card = {
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.2",
                    "body": body_blocks,
                },
            }
        ]
    }

    try:
        resp = requests.post(TEAMS_WEBHOOK_URL, json=card, timeout=10)
        resp.raise_for_status()
        log.info("Teams notification sent (%d failure(s)).", total)
    except Exception as exc:
        log.error("Failed to send Teams notification: %s", exc)


def send_pushover(failures: list, lookback_hours: int) -> None:
    """One Pushover notification per failure — URL opens directly in ADF Monitor."""
    for run in failures:
        start_str = (
            run["run_start"].strftime("%Y-%m-%d %H:%M UTC")
            if run["run_start"] else "—"
        )
        msg = f"{run['env']}  |  Started: {start_str}\n{(run['message'] or '')[:150]}"
        try:
            resp = requests.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token":     PUSHOVER_APP_TOKEN,
                    "user":      PUSHOVER_USER_KEY,
                    "title":     f"ADF FAIL: {run['pipeline_name']}",
                    "message":   msg,
                    "url":       run["url"],
                    "url_title": "Open in ADF Monitor",
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Pushover sent for %s.", run["pipeline_name"])
        except Exception as exc:
            log.error("Failed to send Pushover for %s: %s", run["pipeline_name"], exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    lookback_hours = get_lookback_hours()
    start_time = now - timedelta(hours=lookback_hours)
    generated_at = now.strftime("%Y-%m-%d %H:%M UTC")

    log.info("=" * 70)
    log.info("ADF PIPELINE FAILURE REPORT")
    log.info("Generated : %s", generated_at)
    log.info("Lookback  : %d hours  (%s  →  %s)",
             lookback_hours,
             start_time.strftime("%Y-%m-%d %H:%M UTC"),
             now.strftime("%Y-%m-%d %H:%M UTC"))
    log.info("=" * 70)

    credential = DefaultAzureCredential()
    all_failures = []

    for env_name, config in ENVIRONMENTS.items():
        log.info("")
        log.info("ENVIRONMENT : %s", env_name.upper())
        log.info("Factory     : %s", config["factory_name"])
        log.info("Resource Grp: %s", config["resource_group"])
        log.info("-" * 70)

        try:
            client = DataFactoryManagementClient(credential, SUBSCRIPTION_ID)
            runs = query_failed_runs(
                client,
                config["resource_group"],
                config["factory_name"],
                start_time,
                now,
            )
        except Exception as exc:
            log.error("  Failed to query %s: %s", env_name.upper(), exc)
            continue

        if not runs:
            log.info("  No pipeline failures found.")
        else:
            for run in runs:
                url = monitor_url(run.run_id, config["resource_group"], config["factory_name"])
                log.warning("  Pipeline : %s", run.pipeline_name)
                log.warning("  Run ID   : %s", run.run_id)
                log.warning("  Started  : %s", run.run_start)
                log.warning("  Ended    : %s", run.run_end)
                log.warning("  URL      : %s", url)
                log.warning("  Message  : %s", run.message or "No message provided")
                log.warning("  %s", "-" * 50)
                all_failures.append({
                    "env":           env_name.upper(),
                    "pipeline_name": run.pipeline_name,
                    "run_id":        run.run_id,
                    "run_start":     run.run_start,
                    "run_end":       run.run_end,
                    "message":       run.message or "",
                    "url":           url,
                })

    log.info("")
    log.info("=" * 70)
    log.info("TOTAL FAILURES: %d", len(all_failures))
    log.info("=" * 70)

    if all_failures:
        send_teams(all_failures, lookback_hours, generated_at)
        send_pushover(all_failures, lookback_hours)


if __name__ == "__main__":
    main()
