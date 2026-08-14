from datetime import datetime, timezone

from kubernetes.client.rest import ApiException


def list_database_backups(co_api, namespace):
    """
    Discover CloudNativePG Backup resources in the namespace.
    """
    try:
        response = co_api.list_namespaced_custom_object(
            group="postgresql.cnpg.io",
            version="v1",
            namespace=namespace,
            plural="backups",
        )

        return response.get("items", [])

    except ApiException as error:
        print(f"Failed to list database backups: {error}")
        return []

    except Exception as error:
        print(f"Unexpected error while listing database backups: {error}")
        return []


def get_completed_backups(backups, cluster_name):
    """
    Return only completed backups belonging to the target CNPG cluster.
    """
    completed_backups = []

    for backup in backups:
        metadata = backup.get("metadata", {})
        spec = backup.get("spec", {})
        status = backup.get("status", {})

        backup_name = metadata.get("name", "unknown")

        backup_cluster = (
            spec.get("cluster", {}).get("name")
        )

        phase = status.get("phase")

        # Ignore backups belonging to another cluster.
        if backup_cluster != cluster_name:
            continue

        # Only completed backups are candidates for recovery.
        if phase == "Completed":
            completed_backups.append(backup)

        else:
            print(
                f"Backup {backup_name} is not completed. "
                f"Current phase: {phase}"
            )

    return completed_backups


def find_latest_backup(completed_backups):
    """
    Return the most recently completed backup based on stoppedAt.
    """
    if not completed_backups:
        return None

    valid_backups = []

    for backup in completed_backups:
        stopped_at = (
            backup.get("status", {}).get("stoppedAt")
        )

        if stopped_at:
            valid_backups.append(backup)

    if not valid_backups:
        return None

    return max(
        valid_backups,
        key=lambda backup: backup["status"]["stoppedAt"],
    )


def calculate_backup_age(stopped_at):
    """
    Calculate the age of a backup in hours.
    """
    try:
        backup_time = datetime.fromisoformat(
            stopped_at.replace("Z", "+00:00")
        )

        now_utc = datetime.now(timezone.utc)

        time_difference = now_utc - backup_time

        return time_difference.total_seconds() / 3600

    except (ValueError, TypeError) as error:
        print(f"Invalid backup timestamp '{stopped_at}': {error}")
        return None


def check_database_backups(
    co_api,
    namespace,
    cluster_name,
    rpo_threshold_hours=24,
):
    """
    Check whether a recent completed backup exists for the
    target PostgreSQL cluster and whether it satisfies the RPO.
    """
    backups = list_database_backups(
        co_api,
        namespace,
    )

    if not backups:
        print("No Backup resources were found.")
        return {
            "usable": False,
            "backup_name": None,
            "backup_age_hours": None,
            "reason": "no_backups_found",
        }

    completed_backups = get_completed_backups(
        backups,
        cluster_name,
    )

    if not completed_backups:
        print(
            f"No completed backups were found for "
            f"cluster '{cluster_name}'."
        )

        return {
            "usable": False,
            "backup_name": None,
            "backup_age_hours": None,
            "reason": "no_completed_backup",
        }

    latest_backup = find_latest_backup(
        completed_backups
    )

    if latest_backup is None:
        print(
            f"Completed backups for '{cluster_name}' "
            "do not contain a valid stoppedAt timestamp."
        )

        return {
            "usable": False,
            "backup_name": None,
            "backup_age_hours": None,
            "reason": "missing_backup_timestamp",
        }

    backup_name = (
        latest_backup
        .get("metadata", {})
        .get("name")
    )

    stopped_at = (
        latest_backup
        .get("status", {})
        .get("stoppedAt")
    )

    backup_age_hours = calculate_backup_age(
        stopped_at
    )

    if backup_age_hours is None:
        return {
            "usable": False,
            "backup_name": backup_name,
            "backup_age_hours": None,
            "reason": "invalid_backup_timestamp",
        }

    usable = backup_age_hours <= rpo_threshold_hours

    if usable:
        print(
            f"Backup '{backup_name}' is usable. "
            f"Age: {backup_age_hours:.2f} hours. "
            f"RPO threshold: {rpo_threshold_hours} hours."
        )

        reason = "backup_within_rpo"

    else:
        print(
            f"Backup '{backup_name}' is too old. "
            f"Age: {backup_age_hours:.2f} hours. "
            f"RPO threshold: {rpo_threshold_hours} hours."
        )

        reason = "backup_exceeds_rpo"

    return {
        "usable": usable,
        "backup_name": backup_name,
        "backup_age_hours": backup_age_hours,
        "reason": reason,
    }