import time

from kubernetes.client.rest import ApiException

from health_checker import check_database_health
from backup_checker import check_database_backups


def make_recovery_decision(db_healthy, backup_usable):
    """
    Decide what action should be taken based on database health
    and backup availability.
    """
    if db_healthy:
        return "NO_ACTION"

    if backup_usable:
        return "PROCEED_WITH_RESTORE"

    return "HALT_AND_ALERT"


def run_recovery_assessment(
    v1,
    co_api,
    namespace,
    cluster_name,
):
    """
    Run health and backup checks and determine the recovery action.
    """
    db_healthy = check_database_health(
        v1,
        namespace,
    )

    backup_result = check_database_backups(
        co_api,
        namespace,
        cluster_name,
    )

    backup_usable = backup_result["usable"]
    backup_name = backup_result["backup_name"]
    backup_age = backup_result["backup_age_hours"]

    decision = make_recovery_decision(
        db_healthy,
        backup_usable,
    )

    return {
        "decision": decision,
        "db_healthy": db_healthy,
        "backup_usable": backup_usable,
        "backup_name": backup_name,
        "backup_age_hours": backup_age,
        "backup_reason": backup_result["reason"],
    }


def build_recovery_object(
    cluster_name,
    backup_name,
    instances=3,
):
    """
    Build a CloudNativePG Cluster custom resource for recovery.
    """
    return {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {
            "name": cluster_name,
        },
        "spec": {
            "instances": instances,
            "bootstrap": {
                "recovery": {
                    "backup": {
                        "name": backup_name,
                    }
                }
            },
        },
    }


def recovery_cluster_exists(
    co_api,
    namespace,
    cluster_name,
):
    """
    Check whether the recovery cluster already exists.
    """
    try:
        co_api.get_namespaced_custom_object(
            group="postgresql.cnpg.io",
            version="v1",
            namespace=namespace,
            plural="clusters",
            name=cluster_name,
        )

        return True

    except ApiException as error:
        if error.status == 404:
            return False

        print(
            f"Failed to check recovery cluster existence: {error}"
        )
        return False


def create_recovery_cluster(
    co_api,
    namespace,
    recovery_body,
):
    """
    Create the CloudNativePG recovery cluster.
    """
    try:
        response = co_api.create_namespaced_custom_object(
            group="postgresql.cnpg.io",
            version="v1",
            namespace=namespace,
            plural="clusters",
            body=recovery_body,
        )

        print("Recovery cluster creation request submitted.")
        return response

    except ApiException as error:
        print(
            f"Failed to create recovery cluster: {error}"
        )
        return None


def monitor_recovery(
    co_api,
    namespace,
    cluster_name,
    expected_instances=3,
    max_checks=12,
    interval_seconds=5,
):
    """
    Monitor the recovered CloudNativePG cluster until the expected
    number of instances become ready or the timeout is reached.
    """

    for check_number in range(1, max_checks + 1):

        try:
            cluster = co_api.get_namespaced_custom_object(
                group="postgresql.cnpg.io",
                version="v1",
                namespace=namespace,
                plural="clusters",
                name=cluster_name,
            )

            status = cluster.get("status", {})

            ready_instances = status.get(
                "readyInstances",
                0,
            )

            phase = status.get(
                "phase",
                "Unknown",
            )

            print(
                f"Check {check_number}/{max_checks}: "
                f"phase={phase}, "
                f"ready instances={ready_instances}/"
                f"{expected_instances}"
            )

            if ready_instances >= expected_instances:
                print(
                    "Recovery cluster has the expected "
                    "number of ready instances."
                )
                return True

        except ApiException as error:
            print(
                f"Kubernetes API error while monitoring recovery: "
                f"{error}"
            )

        except Exception as error:
            print(
                f"Unexpected error while monitoring recovery: "
                f"{error}"
            )

        time.sleep(interval_seconds)

    print(
        "Recovery monitoring timed out before the expected "
        "number of instances became ready."
    )

    return False


def orchestrate_recovery(
    v1,
    co_api,
    namespace,
    source_cluster_name,
    recovery_cluster_name="postgres-recovered",
    recovery_instances=3,
):
    """
    Main recovery orchestration pipeline.
    """

    assessment = run_recovery_assessment(
        v1,
        co_api,
        namespace,
        source_cluster_name,
    )

    print("\n--- Recovery Assessment ---")
    print(
        f"Database healthy: "
        f"{assessment['db_healthy']}"
    )
    print(
        f"Backup usable: "
        f"{assessment['backup_usable']}"
    )
    print(
        f"Backup: "
        f"{assessment['backup_name']}"
    )
    print(
        f"Backup age: "
        f"{assessment['backup_age_hours']}"
    )
    print(
        f"Decision: "
        f"{assessment['decision']}"
    )

    decision = assessment["decision"]

    if decision == "NO_ACTION":
        print(
            "\nDatabase is healthy. "
            "No recovery action is required."
        )

        return {
            "decision": decision,
            "recovery_started": False,
            "recovery_successful": False,
            "recovery_cluster": None,
        }

    if decision == "HALT_AND_ALERT":
        print(
            "\nCRITICAL: Database is unhealthy and "
            "no usable backup is available."
        )

        return {
            "decision": decision,
            "recovery_started": False,
            "recovery_successful": False,
            "recovery_cluster": None,
        }

    backup_name = assessment["backup_name"]

    if not backup_name:
        print(
            "\nRecovery cannot proceed because "
            "no backup name was returned."
        )

        return {
            "decision": "HALT_AND_ALERT",
            "recovery_started": False,
            "recovery_successful": False,
            "recovery_cluster": None,
        }

    # Safety check: do not accidentally create a duplicate
    # recovery cluster.
    if recovery_cluster_exists(
        co_api,
        namespace,
        recovery_cluster_name,
    ):
        print(
            f"\nRecovery cluster "
            f"'{recovery_cluster_name}' already exists."
        )

        return {
            "decision": "RECOVERY_CLUSTER_EXISTS",
            "recovery_started": False,
            "recovery_successful": False,
            "recovery_cluster": recovery_cluster_name,
        }

    print(
        f"\nStarting recovery using backup: "
        f"{backup_name}"
    )

    recovery_body = build_recovery_object(
        cluster_name=recovery_cluster_name,
        backup_name=backup_name,
        instances=recovery_instances,
    )

    recovery_cluster = create_recovery_cluster(
        co_api,
        namespace,
        recovery_body,
    )

    if recovery_cluster is None:
        return {
            "decision": "RECOVERY_TRIGGER_FAILED",
            "recovery_started": False,
            "recovery_successful": False,
            "recovery_cluster": recovery_cluster_name,
        }

    recovery_started = True

    recovery_successful = monitor_recovery(
        co_api,
        namespace,
        recovery_cluster_name,
        expected_instances=recovery_instances,
    )

    if recovery_successful:
        print(
            "\nRecovery cluster reached the expected "
            "ready-instance count."
        )
    else:
        print(
            "\nRecovery was triggered, but monitoring "
            "did not confirm completion."
        )

    return {
        "decision": decision,
        "recovery_started": recovery_started,
        "recovery_successful": recovery_successful,
        "recovery_cluster": recovery_cluster_name,
        "backup_name": backup_name,
    }