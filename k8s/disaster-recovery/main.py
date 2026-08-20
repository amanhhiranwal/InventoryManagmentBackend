import os

from kubernetes import client, config

from recovery_manager import orchestrate_recovery


def load_kubernetes_configuration():
    """
    Try local kubeconfig first.
    Fall back to in-cluster configuration.
    """
    try:
        config.load_kube_config()
        print("Successfully loaded local kubeconfig.")
        return True

    except Exception as local_error:
        print(
            f"Could not load local kubeconfig: {local_error}"
        )

    try:
        config.load_incluster_config()
        print("Successfully loaded in-cluster config.")
        return True

    except Exception as cluster_error:
        print(
            f"Could not load in-cluster config: {cluster_error}"
        )
        return False


def main():
    """
    Application entry point.
    """

    if not load_kubernetes_configuration():
        print(
            "Unable to configure Kubernetes access. "
            "Exiting."
        )
        return

    v1 = client.CoreV1Api()
    co_api = client.CustomObjectsApi()

    namespace = os.getenv(
        "K8S_NAMESPACE",
        "inventory-prod",
    )

    source_cluster_name = os.getenv(
        "CNPG_CLUSTER_NAME",
        "postgres-cluster",
    )

    recovery_cluster_name = os.getenv(
        "RECOVERY_CLUSTER_NAME",
        "postgres-recovered",
    )

    recovery_instances = int(
        os.getenv(
            "RECOVERY_INSTANCES",
            "3",
        )
    )

    print(
        "\n--- Starting Disaster Recovery Assessment ---"
    )

    print(
        f"Namespace: {namespace}"
    )

    print(
        f"Source cluster: {source_cluster_name}"
    )

    print(
        f"Recovery cluster: {recovery_cluster_name}"
    )

    print(
        f"Recovery instances: {recovery_instances}"
    )

    result = orchestrate_recovery(
        v1=v1,
        co_api=co_api,
        namespace=namespace,
        source_cluster_name=source_cluster_name,
        recovery_cluster_name=recovery_cluster_name,
        recovery_instances=recovery_instances,
    )

    print("\n--- Final Recovery Result ---")
    print(result)


if __name__ == "__main__":
    main()