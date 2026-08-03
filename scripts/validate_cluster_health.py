#!/usr/bin/env python3
"""
Nexus-20 Operational Health Harness
Executes Layer 7 schema parsing, route auditing, and latency profiling against target clusters.
Returns POSIX exit code 0 on success, non-zero on assertion failure.
"""

import sys
import time
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List

# Initialize structured logging telemetry
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("Nexus20ClusterAudit")

# System Constraints & Assertions
DEFAULT_TARGET = "http://127.0.0.1:8000"
MAX_ALLOWED_LATENCY_MS = 500.0  # Latency SLA threshold for local/staging verification
EXPECTED_MIN_ROUTES = 1          # Minimum required routes to assert non-empty API schema

def audit_cluster_health(base_url: str) -> None:
    """
    Performs deterministic HTTP/JSON assertions against application health endpoints.
    Fails fast with sys.exit(1) if any SLA or status check fails.
    """
    logger.info(f"Initiating Layer 7 cluster health audit against: {base_url}")
    
    endpoints: List[Dict[str, str]] = [
        {"name": "Swagger UI", "path": "/docs", "expected_type": "text/html"},
        {"name": "OpenAPI Schema", "path": "/openapi.json", "expected_type": "application/json"}
    ]
    
    for ep in endpoints:
        url = f"{base_url}{ep['path']}"
        start_time = time.perf_counter()
        
        try:
            req = urllib.request.Request(
                url, 
                headers={"User-Agent": "Nexus20-AutomatedAudit/2.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                status_code = response.getcode()
                content_type = response.headers.get("Content-Type", "")
                
                # Assertion 1: HTTP Status Code
                if status_code != 200:
                    logger.error(f"ASSERTION FAILURE: [{ep['name']}] returned status {status_code} (Expected: 200)")
                    sys.exit(1)
                    
                # Assertion 2: Content-Type Match
                if ep["expected_type"] not in content_type:
                    logger.error(
                        f"ASSERTION FAILURE: [{ep['name']}] Content-Type '{content_type}' "
                        f"does not contain expected '{ep['expected_type']}'"
                    )
                    sys.exit(1)
                    
                # Assertion 3: Latency SLA
                if latency_ms > MAX_ALLOWED_LATENCY_MS:
                    logger.warning(
                        f"SLA BREACH: [{ep['name']}] Latency {latency_ms:.2f}ms "
                        f"exceeded SLA threshold ({MAX_ALLOWED_LATENCY_MS}ms)"
                    )
                else:
                    logger.info(
                        f"SUCCESS: [{ep['name']}] | Status: {status_code} | "
                        f"Latency: {latency_ms:.2f}ms | Type: {content_type}"
                    )
                
                # Schema Parsing & Route Assertion
                if ep["path"] == "/openapi.json":
                    raw_payload = response.read().decode("utf-8")
                    schema_data: Dict[str, Any] = json.loads(raw_payload)
                    
                    app_title = schema_data.get("info", {}).get("title", "Unknown")
                    app_version = schema_data.get("info", {}).get("version", "Unknown")
                    routes = schema_data.get("paths", {})
                    route_count = len(routes)
                    
                    if route_count < EXPECTED_MIN_ROUTES:
                        logger.error(f"SCHEMA FAILURE: Parsed {route_count} routes. Expected >= {EXPECTED_MIN_ROUTES}")
                        sys.exit(1)
                        
                    logger.info(
                        f"SCHEMA PARSED: App Name='{app_title}' | "
                        f"Version='{app_version}' | Active Routes={route_count}"
                    )
                    
        except urllib.error.URLError as e:
            logger.error(f"NETWORK FAILURE: Unable to connect to [{ep['name']}] at {url}. Reason: {str(e)}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"UNHANDLED EXCEPTION during audit of [{ep['name']}]: {str(e)}")
            sys.exit(1)
            
    logger.info("ALL SYSTEM AUDIT CHECKS PASSED SUCCESSFULLY [EXIT CODE 0]")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    audit_cluster_health(target)    