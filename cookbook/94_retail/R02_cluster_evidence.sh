#!/bin/bash
# R2 -- the four cluster scenarios, by hand, against kind.
#
# The package's evidence: all pods running; a granted call 200 and an ungranted call 403; the
# killed api pod is replaced without intervention; the consolidator refuses to scale. Plus the
# migrate hook Job's log. Every scenario prints what it saw; the summary at the end asserts.
#
# Shell, not Python, on purpose: this is kubectl and helm end to end, and wrapping it in
# subprocess.run to satisfy a file extension would make it harder to read and rerun by hand.
#
# Prerequisites:
#   docker build -f deploy/Dockerfile -t egpt-retail:dev .
#   kind create cluster --name egpt --config deploy/kind/cluster.yaml
#   kind load docker-image egpt-retail:dev --name egpt
#
# Run:
#   bash cookbook/94_retail/R02_cluster_evidence.sh
#
# Environment:
#   KUBE_CONTEXT       default kind-egpt. On AKS day, the AKS context.
#   NAMESPACE          default "default" (where kind installs land). AKS_CUTOVER.md §3 installs
#                      into "egpt" -- pass NAMESPACE=egpt there, or every command below targets
#                      the wrong namespace and the in-cluster http://egpt-api:7777 never resolves.
#   VALUES_FILE        default deploy/helm/egpt/values.local.yaml. On AKS: values.azure.yaml --
#                      values.local.yaml would replace Entra's JWKS with an offline key, the
#                      keyVault secrets provider with the plain Secret, and pullPolicy with Never.
#   SKIP_INFRA         unset by default (installs deploy/helm/egpt-local-infra, step 1). Set to
#                      skip it: AKS_CUTOVER.md says explicitly not to install it -- Postgres,
#                      Redis etc. are the managed Azure services already provisioned.
#   TOKEN_GRANTED,
#   TOKEN_UNGRANTED    unset by default (step 2 mints both offline via devauth against a fresh
#                      key pair, and step 3 mounts that pair's JWKS). Set both to mint from Entra
#                      instead and skip devauth and the --set-file auth.jwks flag -- values.azure.yaml
#                      already carries Entra's JWKS from the initial install (AKS_CUTOVER.md §3).
#   AUTH_DIR           default a temp dir; where devauth writes private.pem and jwks.json. Unused
#                      when TOKEN_GRANTED/TOKEN_UNGRANTED are both set.

set -u
cd "$(dirname "$0")/../.."
PY=${PY:-.venv/bin/python}
CTX=${KUBE_CONTEXT:-kind-egpt}
NAMESPACE=${NAMESPACE:-default}
VALUES_FILE=${VALUES_FILE:-deploy/helm/egpt/values.local.yaml}
K="kubectl --context $CTX -n $NAMESPACE"
H="helm --kube-context $CTX --namespace $NAMESPACE"
AUTH_DIR=${AUTH_DIR:-$(mktemp -d)}
TOKEN_GRANTED=${TOKEN_GRANTED:-}
TOKEN_UNGRANTED=${TOKEN_UNGRANTED:-}
AUD="api://egpt-retail"
FAIL=()
step() { echo; echo "################ $1 ################"; }
check() { if [ "$1" = "1" ]; then echo "   OK   $2"; else echo "   FAIL $2"; FAIL+=("$2"); fi; }

step "0. cluster"
$K get nodes -o wide | sed 's/^/   /'

step "1. infrastructure chart first (the migrate hook needs Postgres)"
if [ -n "${SKIP_INFRA:-}" ]; then
  echo "   SKIP_INFRA set; assuming the managed services (Flexible Server, Cache for Redis, ...) already exist"
else
  $H upgrade --install egpt-infra deploy/helm/egpt-local-infra --create-namespace --wait --timeout 300s 2>&1 | tail -2 | sed 's/^/   /'
  $K get pods -l 'app in (egpt-postgres,egpt-pgbouncer)' -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase | sed 's/^/   /'
fi

step "2. offline identity: RS256 key pair + JWKS"
if [ -n "$TOKEN_GRANTED" ] && [ -n "$TOKEN_UNGRANTED" ]; then
  echo "   TOKEN_GRANTED/TOKEN_UNGRANTED provided; skipping devauth (tokens minted from Entra on AKS)"
else
  $PY -m egpt_retail.app.devauth keygen "$AUTH_DIR" | sed 's/^/   /'
  TOKEN_GRANTED=$($PY -m egpt_retail.app.devauth mint "$AUTH_DIR/private.pem" --sub alice --scope config:read --aud "$AUD")
  TOKEN_UNGRANTED=$($PY -m egpt_retail.app.devauth mint "$AUTH_DIR/private.pem" --sub bob --scope agents:read --aud "$AUD")
  echo "   alice: scopes=[config:read]   bob: scopes=[agents:read]"
fi

step "3. application chart (pre-install hook runs migrate first)"
if $H status egpt 2>/dev/null | grep -q "STATUS: failed"; then
  case "$CTX" in
    kind-*)
      echo "   previous release is failed; uninstalling so the install path runs clean"
      $H uninstall egpt >/dev/null 2>&1
      ;;
    *)
      echo "   previous release is failed on non-kind context '$CTX'; not auto-uninstalling."
      FAIL+=("release egpt is failed on $CTX/$NAMESPACE -- investigate by hand ('helm status egpt --kube-context $CTX -n $NAMESPACE') before re-running")
      ;;
  esac
fi
JWKS_ARG=""
if [ -f "$AUTH_DIR/jwks.json" ]; then
  # A single `--flag=value` word, not `--set-file "value"` as two: an array is the usual way to
  # keep an optional argument out of a command line, but bash 3.2 (macOS's /bin/bash) treats
  # expanding an empty array under `set -u` as an unbound-variable error, aborting the run.
  JWKS_ARG="--set-file=auth.jwks=$AUTH_DIR/jwks.json"
fi
$H upgrade --install egpt deploy/helm/egpt -f "$VALUES_FILE" --create-namespace \
  $JWKS_ARG --wait --timeout 300s 2>&1 | tail -2 | sed 's/^/   /'
echo "   --- migrate hook Job log (tail)"
$K logs job/egpt-migrate 2>&1 | tail -4 | sed 's/^/   /'
echo "   --- all pods"
$K get pods -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount | sed 's/^/   /'
NOT_RUNNING=$($K get pods --field-selector=status.phase!=Running,status.phase!=Succeeded --no-headers 2>/dev/null | wc -l | tr -d ' ')
check "$([ "$NOT_RUNNING" = "0" ] && echo 1 || echo 0)" "all pods running (or completed)"

# A helper pod for in-cluster HTTP. port-forward pins to one pod and dies with it, so the pod-kill
# loop below must run from inside the cluster against the Service's ClusterIP.
$K delete pod egpt-curl --ignore-not-found --wait=true >/dev/null 2>&1
$K run egpt-curl --image=curlimages/curl:8.10.1 --restart=Never --command -- sleep 3600 >/dev/null
$K wait --for=condition=Ready pod/egpt-curl --timeout=120s >/dev/null
API=http://egpt-api:7777
curl_in() { $K exec egpt-curl -- curl -s -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }

step "4. authorization: 200 for a granted scope, 403 for an ungranted one, /health open"
C_HEALTH=$(curl_in "$API/health")
C_GRANTED=$(curl_in -H "Authorization: Bearer $TOKEN_GRANTED" "$API/config")
C_UNGRANTED=$(curl_in -H "Authorization: Bearer $TOKEN_UNGRANTED" "$API/config")
C_NOTOKEN=$(curl_in "$API/config")
echo "   GET /health (no token)             -> $C_HEALTH"
echo "   GET /config alice [config:read]    -> $C_GRANTED"
echo "   GET /config bob   [agents:read]    -> $C_UNGRANTED"
echo "   GET /config (no token)             -> $C_NOTOKEN"
echo "   --- 403 body:"; $K exec egpt-curl -- curl -s -H "Authorization: Bearer $TOKEN_UNGRANTED" "$API/config" 2>/dev/null | head -c 300 | sed 's/^/   /'; echo
echo "   --- 200 body (agents):"; $K exec egpt-curl -- curl -s -H "Authorization: Bearer $TOKEN_GRANTED" "$API/config" 2>/dev/null | $PY -c "import json,sys; raw=sys.stdin.read(); d=json.loads(raw) if raw.strip() else {}; print('   os_id', d.get('os_id'), '| agents', [a['id'] for a in d.get('agents',[])])"
check "$([ "$C_HEALTH" = "200" ] && echo 1 || echo 0)" "/health is open without a token (by design, DEFAULT_EXCLUDED_ROUTE_PATHS)"
check "$([ "$C_GRANTED" = "200" ] && echo 1 || echo 0)" "granted scope -> 200"
check "$([ "$C_UNGRANTED" = "403" ] && echo 1 || echo 0)" "ungranted scope -> 403"
check "$([ "$C_NOTOKEN" = "401" ] || [ "$C_NOTOKEN" = "403" ] && echo 1 || echo 0)" "no token -> $C_NOTOKEN (refused)"

step "5. kill an api pod under load: the evidence is the status-code log, not kubectl get pods"
API_PODS_BEFORE=$($K get pods -l app=egpt-api -o name | sort)
echo "   api pods before: $(echo "$API_PODS_BEFORE" | tr '\n' ' ')"
# 40 seconds of requests every 200ms from inside the cluster, written to the helper pod.
$K exec egpt-curl -- sh -c 'rm -f /tmp/codes; end=$(( $(date +%s) + 40 )); while [ $(date +%s) -lt $end ]; do curl -s -o /dev/null -w "%{http_code}\n" --max-time 2 http://egpt-api:7777/health >> /tmp/codes; sleep 0.2; done' >/dev/null 2>&1 &
LOOP_PID=$!
sleep 5
VICTIM=$($K get pods -l app=egpt-api -o name | head -1)
echo "   deleting $VICTIM at $(date +%T)"
$K delete "$VICTIM" --wait=false >/dev/null
wait $LOOP_PID
CODES=$($K exec egpt-curl -- cat /tmp/codes 2>/dev/null)
TOTAL=$(echo "$CODES" | grep -c . || true)
NON200=$(echo "$CODES" | grep -vc '^200$' || true)
echo "   requests: $TOTAL   non-200: $NON200   distinct codes: $(echo "$CODES" | sort | uniq -c | tr '\n' ' ')"
$K rollout status deploy/egpt-api --timeout=120s 2>&1 | tail -1 | sed 's/^/   /'
API_PODS_AFTER=$($K get pods -l app=egpt-api -o name | sort)
echo "   api pods after : $(echo "$API_PODS_AFTER" | tr '\n' ' ')"
REPLACED=$([ "$API_PODS_BEFORE" != "$API_PODS_AFTER" ] && [ "$(echo "$API_PODS_AFTER" | wc -l | tr -d ' ')" = "2" ] && echo 1 || echo 0)
check "$REPLACED" "the killed pod was replaced without intervention (2 replicas again, a new name)"
check "$([ "$TOTAL" -gt 100 ] && [ "$NON200" = "0" ] && echo 1 || echo 0)" "no non-200 during the kill ($NON200 of $TOTAL)"

step "6. the consolidator refuses to scale"
$K scale deploy/egpt-consolidator --replicas=2 >/dev/null
echo "   kubectl scale --replicas=2 accepted (a chart cannot stop this); waiting for the second pod..."
for i in $(seq 1 30); do
  SECOND=$($K get pods -l app=egpt-consolidator -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].lastState.terminated.exitCode}{" "}{.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}' | grep -E ' 3 | CrashLoopBackOff' | head -1)
  [ -n "$SECOND" ] && break; sleep 2
done
$K get pods -l app=egpt-consolidator -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,LASTEXIT:.status.containerStatuses[0].lastState.terminated.exitCode,WAITING:.status.containerStatuses[0].state.waiting.reason | sed 's/^/   /'
REFUSED_POD=$($K get pods -l app=egpt-consolidator -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}{end}' | awk '$2==3{print $1}' | head -1)
if [ -n "$REFUSED_POD" ]; then echo "   --- log of the refused pod:"; $K logs "$REFUSED_POD" --previous 2>/dev/null | grep "consolidator:" | tail -2 | sed 's/^/   /'; fi
check "$([ -n "$REFUSED_POD" ] && echo 1 || echo 0)" "second consolidator exits 3 naming the customer; the deployment cannot reach 2 healthy"
$K scale deploy/egpt-consolidator --replicas=1 >/dev/null
echo "   --- the surviving consolidator's log:"; $K logs deploy/egpt-consolidator 2>/dev/null | grep "consolidator:" | head -3 | sed 's/^/   /'

step "7. helm render guard: a replica count on the consolidator refuses to template"
GUARD=$(helm template egpt deploy/helm/egpt --set consolidator.replicaCount=2 2>&1 | grep -c "is not a setting" || true)
check "$([ "$GUARD" -ge 1 ] && echo 1 || echo 0)" "helm template fails on consolidator.replicaCount"

$K delete pod egpt-curl --ignore-not-found --wait=false >/dev/null 2>&1
echo
echo "################ summary ################"
if [ ${#FAIL[@]} -eq 0 ]; then echo "All R2 cluster scenarios passed."; exit 0; fi
echo "FAILED:"; for f in "${FAIL[@]}"; do echo "  - $f"; done; exit 1
