import os

import pg8000.dbapi
from kubernetes import client, config
from kubernetes.client.rest import ApiException


# ---------------------------------------------------------
# Kubernetes connection
# ---------------------------------------------------------

def connect_to_kubernetes():
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        return v1

    except ApiException as error:
        print(f"Kubernetes connection unsuccessful: {error}")
        return None

    except Exception as general_error:
        print(f"An unexpected error occurred: {general_error}")
        return None


# ---------------------------------------------------------
# Get PostgreSQL pods
# ---------------------------------------------------------

def get_postgres_pods(v1, namespace, cluster_name):
    try:
        pod_list = v1.list_namespaced_pod(namespace=namespace)

        postgres_pods = []

        for pod in pod_list.items:
            labels = pod.metadata.labels

            if labels is None:
                continue

            if labels.get("cnpg.io/cluster") == cluster_name:
                postgres_pods.append(pod)

        return postgres_pods

    except ApiException as error:
        print(
            "Alert: Failed to list pods in namespace. "
            f"Check RBAC permissions or namespace spelling. Details: {error}"
        )
        return []


# ---------------------------------------------------------
# Check pod phases 
# ---------------------------------------------------------

def check_pod_phases(postgres_pods):
    unhealthy_pods = []

    for pod in postgres_pods:
        name = pod.metadata.name
        phase = pod.status.phase

        print(f"Pod {name} → {phase}")

        # Succeeded is acceptable for completed job/init pods,
        # but actual CNPG instance pods should normally be Running.
        pod_role = pod.metadata.labels.get("cnpg.io/podRole")

        if pod_role == "instance":
            if phase != "Running":
                print(
                    f"Unhealthy PostgreSQL instance pod: "
                    f"{name} ({phase})"
                )
                unhealthy_pods.append(pod)

    return unhealthy_pods


# ---------------------------------------------------------
# Check container readiness
# ---------------------------------------------------------

def check_container_readiness(postgres_pods):
    not_ready_pods = []

    for pod in postgres_pods:
        name = pod.metadata.name
        container_statuses = pod.status.container_statuses

        if container_statuses is None:
            print(f"Pod {name} has no container status information.")
            not_ready_pods.append(pod)
            continue

        all_ready = True

        for container in container_statuses:
            if not container.ready:
                print(
                    f"Container {container.name} in pod {name} "
                    "is not ready."
                )
                all_ready = False

        if all_ready:
            print(f"All containers in pod {name} are ready.")

        else:
            not_ready_pods.append(pod)

    return not_ready_pods


# ---------------------------------------------------------
# Check container restarts
# ---------------------------------------------------------

def check_container_restarts(postgres_pods, threshold=3):
    warning_pods = []

    for pod in postgres_pods:
        name = pod.metadata.name
        container_statuses = pod.status.container_statuses

        if container_statuses is None:
            continue

        for container in container_statuses:
            restart_count = container.restart_count

            if restart_count > threshold:
                print(
                    f"Warning: Container {container.name} in pod {name} "
                    f"has restarted {restart_count} times."
                )
                warning_pods.append(pod)

    return warning_pods


# ---------------------------------------------------------
# Identify primary / replica
# ---------------------------------------------------------

def identify_pod_roles(postgres_pods):
    primary_pod = None
    replica_pods = []

    for pod in postgres_pods:
        labels = pod.metadata.labels or {}

        role = labels.get("cnpg.io/instanceRole")
        name = pod.metadata.name

        if role == "primary":
            print(f"Pod {name} is a primary pod.")
            primary_pod = pod

        elif role == "replica":
            print(f"Pod {name} is a replica pod.")
            replica_pods.append(pod)

        else:
            print(f"Pod {name} has an unknown role.")

    return primary_pod, replica_pods


# ---------------------------------------------------------
# PostgreSQL connection
# ---------------------------------------------------------

def connect_to_postgres(host, user, password, database, port=5432):
    try:
        connection = pg8000.dbapi.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
        )

        print("PostgreSQL connection successful.")
        return connection

    except pg8000.dbapi.InterfaceError as error:
        print(f"PostgreSQL connection unsuccessful: {error}")
        return None

    except pg8000.dbapi.DatabaseError as error:
        print(f"PostgreSQL database error: {error}")
        return None


# ---------------------------------------------------------
# Database ping
# ---------------------------------------------------------

def run_db_ping(connection):
    try:
        cursor = connection.cursor()

        cursor.execute("SELECT 1;")

        result = cursor.fetchone()

        cursor.close()

        if result and result[0] == 1:
            print("Database ping successful.")
            return True

        print("Database ping returned an unexpected result.")
        return False

    except pg8000.dbapi.DatabaseError as error:
        print(f"Database ping failed: {error}")
        return False


# ---------------------------------------------------------
# Main health checker
# ---------------------------------------------------------

def run_health_checker():

    # -------------------------
    # Configuration
    # -------------------------

    namespace = "inventory-prod"
    cluster_name = "postgres-cluster"

    restart_threshold = 3

    host = "postgres-cluster-rw"
    database = "inventory_db"

    user = os.getenv("PGUSER")
    password = os.getenv("PGPASSWORD")

    if not user or not password:
        print(
            "PostgreSQL credentials are missing. "
            "Set PGUSER and PGPASSWORD environment variables."
        )
        return False

    # -------------------------
    # Kubernetes
    # -------------------------

    v1 = connect_to_kubernetes()

    if v1 is None:
        print("Kubernetes connection failed. Exiting health checker.")
        return False

    # -------------------------
    # Find PostgreSQL pods
    # -------------------------

    postgres_pods = get_postgres_pods(
        v1,
        namespace,
        cluster_name,
    )

    if not postgres_pods:
        print("No PostgreSQL cluster pods were found.")
        return False

    print(
        f"\nFound {len(postgres_pods)} PostgreSQL cluster pods.\n"
    )

    # -------------------------
    # Pod phase check
    # -------------------------

    unhealthy_pods = check_pod_phases(postgres_pods)

    # Only continue deeper with running instance pods
    healthy_phase_pods = [
        pod
        for pod in postgres_pods
        if pod.status.phase == "Running"
        and (pod.metadata.labels or {}).get("cnpg.io/podRole") == "instance"
    ]

    if unhealthy_pods:
        print(
            f"\nUnhealthy PostgreSQL instance pods: "
            f"{len(unhealthy_pods)}"
        )

    # -------------------------
    # Container readiness
    # -------------------------

    not_ready_pods = check_container_readiness(
        healthy_phase_pods
    )

    # -------------------------
    # Restart warnings
    # -------------------------

    warning_pods = check_container_restarts(
        healthy_phase_pods,
        restart_threshold,
    )

    # -------------------------
  .;l
    # -------------------------

    primary_pod, replica_pods = identify_pod_roles(
        healthy_phase_pods
    )

    if primary_pod is None:
        print("\nNo healthy primary PostgreSQL pod found.")
        return False

    # -------------------------
    # Database connection and ping
    # -------------------------

    print(
        f"\nConnecting to PostgreSQL through "
        f"{host}:5432..."
    )

    connection = connect_to_postgres(
        host=host,
        user=user,
        password=password,
        database=database,
    )

    if connection is None:
        print("Database connection failed.")
        return False

    # -------------------------
    # Database ping
    # -------------------------

    database_healthy = run_db_ping(connection)

    connection.close()

    if not database_healthy:
        print("Database health check failed.")
        return False

    # -------------------------
    # Final resultt
    # -------------------------

    print("\n--------------------------------")
    print("POSTGRESQL HEALTH CHECK RESULT")
    print("--------------------------------")
    print(f"Total PostgreSQL pods : {len(postgres_pods)}")
    print(f"Healthy primary       : {primary_pod.metadata.name}")
    print(f"Replica count         : {len(replica_pods)}")
    print(f"Unhealthy pods        : {len(unhealthy_pods)}")
    print(f"Not-ready pods        : {len(not_ready_pods)}")
    print(f"Restart warnings      : {len(warning_pods)}")
    print(f"Database ping         : {'PASS' if database_healthy else 'FAIL'}")

    if unhealthy_pods or not_ready_pods or not database_healthy:
        print("\nOverall result: UNHEALTHY")
        return False

    if warning_pods:
        print("\nOverall result: HEALTHY WITH WARNINGS")
        return True

    print("\nOverall result: HEALTHY")
    return True


if __name__ == "__main__":
    run_health_checker()