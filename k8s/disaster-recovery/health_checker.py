from kubernetes import client,config
import pg8000.dbapi
from kubernetes.client.exceptions import ApiException

def connect_to_kubernetes():
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        return v1
    except ApiException as error:
        print(f"Kubernetes Connection Unsuccessful: {error}")
        return None
    except Exception as general_error:
        # 4. A fallback exception handler in case of non-Kubernetes errors (like a missing config file)
        print(f"An unexpected error occurred: {general_error}")
        return None

def get_postgres_pods(v1, namespace):
    try:
        postgres_pods = v1.list_namespace_pod(namespace = namespace)
        for pod in postgres_pods:
            label = pod.metadata.label
            if label is None:
                continue
            if label.get("cnpg.io/cluster") == "postgres-cluster":
                print(pod)  #THERE ARE SOME CHANGES HERE


    except ApiException as e:
       print(f"Alert: Failed to list pods in namespace! Check your RBAC permissions or namespace spelling. Details: {e}")
       return []  # Return empty list safely so loops don't break

def check_pod_phases(postgres_pods):
    try:
       for pod in postgres_pods:
        name = pod.status.name
        phase = pod.status.phase
        if phase != "Running":
            print(f"Unhealthy pod{name} ")
    except AttributeError as error:
        print(".status is missin")

def check_container_readiness(postgres_pods):
    
    for pod in postgres_pods:
        all_ready = True
        if pod.status.container_statuses is None:
            continue

        for container in pod.status.container_statuses:
            if container != container.ready:
                all_ready = False

            if all_ready:
                print(f"Container ready? = {all_ready} ")

def check_container_restarts(postgres_pods, threshold=3):
    for pod in postgres_pods:
        for container in pod.status.container_statuses:
            if pod.status.container_statuses is None:
                continue
            if container.restart_count > threshold:
                print(f"{container} exceeded the threshold value")




        



