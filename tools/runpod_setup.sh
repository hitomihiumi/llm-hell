#!/usr/bin/env bash
# Downloads the two model checkpoints (GLM 4.7 AWQ 4-bit + GLM 4.7 Flash
# FP8) and launches both as vLLM OpenAI-compatible servers on a RunPod GPU
# pod. Assumes the pod image already has Python, CUDA 13, and vLLM
# preinstalled - this script does NOT install/reinstall vLLM or torch,
# only the Hugging Face download tooling. It does NOT set up the LLM-Hell
# backend itself (that runs on a separate VPS, not here).
#
# Safe to rerun: always kills any previously running vLLM server
# processes (by pid file *and* by a broad `pkill -f "vllm serve"`, since
# the image might have its own preexisting vLLM process too) before
# starting new ones, so ports 8000/8001 don't stay stuck.
#
# Default GPU split assumes 4x GPUs: 3 for the planner (GLM 4.7), 1 for
# the flash executor (GLM 4.7 Flash). Edit the CONFIG block below if your
# pod's GPU count/topology differs.
#
# Usage: bash runpod_setup.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# CONFIG - edit before running if your setup differs
# ---------------------------------------------------------------------------

PLANNER_MODEL_REPO="QuantTrio/GLM-4.7-AWQ"
PLANNER_SERVED_NAME="glm-4.7"
PLANNER_PORT=8000
PLANNER_GPUS="0,1,2"
PLANNER_TP_SIZE=3
PLANNER_QUANTIZATION="awq"
PLANNER_KV_CACHE_DTYPE="fp8"

FLASH_MODEL_REPO="unsloth/GLM-4.7-Flash-FP8-Dynamic"
FLASH_SERVED_NAME="glm-4.7-flash"
FLASH_PORT=8001
FLASH_GPUS="3"
FLASH_TP_SIZE=1
FLASH_QUANTIZATION=""  # weights are already FP8 in the checkpoint - vLLM auto-detects
# --kv-cache-dtype fp8 is known to make this specific checkpoint loop/repeat
# garbage output on vLLM (https://huggingface.co/unsloth/GLM-4.7-Flash-FP8-Dynamic/discussions/2,
# unresolved as of writing, and unrelated to the CUDA version - it's a
# checkpoint/kernel issue). Leave at "auto" unless you've verified fp8
# works on your vLLM build with a short manual test.
FLASH_KV_CACHE_DTYPE="auto"
# The actual weights are only ~31 GiB on disk, but the process OOMed at
# ~94.5 GiB on a 96 GiB card even at a modest max-model-len - the
# unaccounted ~60 GiB is very likely CUDA graph capture (vLLM profiles a
# max-batch forward pass and captures graphs across many batch-size
# buckets) plus chunked-prefill's own working set, not the weights
# themselves. --enforce-eager skips graph capture entirely, trading some
# throughput for a guaranteed lower memory footprint - set to "" to
# re-enable graphs once you've confirmed there's headroom to spare
# (e.g. after giving flash 2 GPUs instead of 1).
FLASH_ENFORCE_EAGER="true"

# Separate max-model-len per server, not a shared one: the planner has 3
# GPUs (288 GiB) to spread weights + KV cache across, flash has 1 (96 GiB)
# - the same 200k-token context that fits fine on the planner OOMs flash
# during KV cache allocation, since its weights + CUDA-graph capture
# buffers alone already use nearly the whole card. Confirmed by the
# actual crash: "CUDA out of memory ... this process has 94.57 GiB memory
# in use" out of 94.97 GiB, failing on the KV cache tensor allocation
# specifically (i.e. after weight loading, right where it needs headroom).
# Raise FLASH_MAX_MODEL_LEN only after giving flash more GPUs (increase
# FLASH_TP_SIZE and FLASH_GPUS, taking GPUs away from the planner) or
# lowering GPU_MEM_UTILIZATION won't fix it - there's just not enough
# room left after the weights on a single card at 200k context.
PLANNER_MAX_MODEL_LEN=200000
FLASH_MAX_MODEL_LEN=65536
GPU_MEM_UTILIZATION=0.90
# The FIRST run on a given pod needs much longer than model loading alone:
# FlashInfer JIT-compiles/downloads its kernel cubins for this GPU arch on
# first use, and there are thousands of them. Subsequent runs reuse
# ~/.cache/flashinfer and are much faster - lower this once you've
# confirmed a cold start actually completes.
HEALTH_TIMEOUT_SECONDS=300

# Escape hatch: if a server's log shows FlashInfer/PTX/JIT compile errors
# (distinct from the benign "SM 12.x requires CUDA >= 12.9" notice, which
# on a properly CUDA-13 pod shouldn't even appear), set this to
# "FLASH_ATTN" (or "XFORMERS") and rerun - vLLM reads this as an env var,
# not a CLI flag.
VLLM_ATTENTION_BACKEND_OVERRIDE=""

LOG_DIR="/var/log/vllm"

# ---------------------------------------------------------------------------

mkdir -p "$LOG_DIR"

echo "==> Killing any process currently holding GPU memory"
# Authoritative source, not a command-line guess: ask nvidia-smi directly
# for every PID with an active GPU context and kill all of them. This is
# what actually matters - a previous run's `vllm serve` (the API server)
# getting killed does NOT kill its spawned EngineCore/Worker subprocesses,
# since vLLM renames those via setproctitle to "VLLM::EngineCore" /
# "VLLM::Worker" rather than inheriting the "vllm serve ..." command
# line, so a pattern-matching `pkill -f "vllm serve"` alone leaves them
# orphaned, silently holding the GPU's memory across every rerun.
gpu_pids="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ' | grep -v '^$' || true)"
if [ -n "$gpu_pids" ]; then
    echo "$gpu_pids" | while read -r pid; do
        echo "   killing pid $pid (holding GPU memory)"
        kill -9 "$pid" 2>/dev/null || true
    done
fi
# Belt-and-suspenders for anything nvidia-smi didn't catch (e.g. a process
# mid-startup, not yet holding a CUDA context).
pkill -9 -f "vllm serve" 2>/dev/null || true
pkill -9 -f "VLLM::" 2>/dev/null || true
for name in planner flash; do
    if [ -f "$LOG_DIR/$name.pid" ]; then
        old_pid="$(cat "$LOG_DIR/$name.pid")"
        kill -9 "$old_pid" 2>/dev/null || true
        rm -f "$LOG_DIR/$name.pid"
    fi
done
sleep 5

echo "==> GPUs visible on this pod:"
nvidia-smi -L || { echo "!! nvidia-smi not found - is this actually a GPU pod?" >&2; exit 1; }

echo "==> GPU memory after cleanup (should be ~0 used on every line):"
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv
still_held="$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null || true)"
if [ -n "$still_held" ]; then
    echo "!! GPU memory is still held after the kill step:" >&2
    echo "$still_held" >&2
    echo "!! Starting new servers now would likely OOM again - investigate before continuing." >&2
    exit 1
fi

echo "==> vLLM already installed on this image:"
vllm --version || { echo "!! vllm not found on PATH - is this actually the right image?" >&2; exit 1; }

echo "==> Installing Hugging Face download tooling"
pip install -q -U uv
# --break-system-packages: fine on a throwaway pod rebuilt from the image
# rather than hand-maintained; do not use this on a machine you upgrade
# in place.
uv pip install --system --break-system-packages -U "huggingface_hub[cli]" hf_transfer

export HF_HUB_ENABLE_HF_TRANSFER=1

echo "==> Downloading $PLANNER_MODEL_REPO"
hf download "$PLANNER_MODEL_REPO" 2>&1 | tee "$LOG_DIR/download-planner.log"

echo "==> Downloading $FLASH_MODEL_REPO"
hf download "$FLASH_MODEL_REPO" 2>&1 | tee "$LOG_DIR/download-flash.log"

start_vllm() {
    local name="$1" model="$2" served_name="$3" port="$4" gpus="$5" tp="$6" quant="$7" kv_dtype="$8" max_len="$9"
    local enforce_eager="${10}"

    echo "==> Starting $name ($model) on GPUs [$gpus], port $port, max-model-len $max_len"
    # shellcheck disable=SC2086
    CUDA_VISIBLE_DEVICES="$gpus" \
    ${VLLM_ATTENTION_BACKEND_OVERRIDE:+VLLM_ATTENTION_BACKEND="$VLLM_ATTENTION_BACKEND_OVERRIDE"} \
    nohup vllm serve "$model" \
        --served-model-name "$served_name" \
        --tensor-parallel-size "$tp" \
        ${quant:+--quantization "$quant"} \
        --kv-cache-dtype "$kv_dtype" \
        --tool-call-parser glm47 \
        --reasoning-parser glm45 \
        --enable-auto-tool-choice \
        --trust-remote-code \
        --max-model-len "$max_len" \
        --gpu-memory-utilization "$GPU_MEM_UTILIZATION" \
        ${enforce_eager:+--enforce-eager} \
        --host 0.0.0.0 \
        --port "$port" \
        > "$LOG_DIR/$name.log" 2>&1 &

    echo $! > "$LOG_DIR/$name.pid"
}

start_vllm "planner" "$PLANNER_MODEL_REPO" "$PLANNER_SERVED_NAME" "$PLANNER_PORT" \
    "$PLANNER_GPUS" "$PLANNER_TP_SIZE" "$PLANNER_QUANTIZATION" "$PLANNER_KV_CACHE_DTYPE" "$PLANNER_MAX_MODEL_LEN" ""

start_vllm "flash" "$FLASH_MODEL_REPO" "$FLASH_SERVED_NAME" "$FLASH_PORT" \
    "$FLASH_GPUS" "$FLASH_TP_SIZE" "$FLASH_QUANTIZATION" "$FLASH_KV_CACHE_DTYPE" "$FLASH_MAX_MODEL_LEN" "$FLASH_ENFORCE_EAGER"

_diagnose_known_failures() {
    local logfile="$1"
    # Patterns are deliberately specific (not bare words like "error" or
    # "not supported") - those match unrelated log noise.
    if grep -qiE "libnvptxcompiler|ptxas fatal|PTX JIT (failed|error)" "$logfile"; then
        echo "   -> looks like a FlashInfer/PTX-JIT compile failure." >&2
        echo "      Try setting VLLM_ATTENTION_BACKEND_OVERRIDE=\"FLASH_ATTN\" at the top of this script and rerun." >&2
    fi
    if grep -qiE "unrecognized model type|Model architectures .* are not supported|glm4_moe_lite" "$logfile"; then
        echo "   -> looks like GLM-4.7-Flash's architecture isn't recognized by this vLLM build." >&2
        echo "      Check 'vllm --version' above against what GLM-4.7-Flash actually needs upstream." >&2
    fi
    if grep -qiE "CUDA out of memory|OutOfMemoryError" "$logfile"; then
        echo "   -> GPU ran out of memory (weights + CUDA-graph buffers left no room for KV cache)." >&2
        echo "      Lower PLANNER_MAX_MODEL_LEN/FLASH_MAX_MODEL_LEN, or give that server more GPUs" >&2
        echo "      (raise its *_TP_SIZE and *_GPUS), rather than GPU_MEM_UTILIZATION - the crash is" >&2
        echo "      real physical VRAM pressure, not vLLM being too conservative about a soft limit." >&2
    fi
}

wait_for_health() {
    local name="$1" port="$2"
    local waited=0

    echo "==> Waiting for $name to become healthy (timeout ${HEALTH_TIMEOUT_SECONDS}s)"
    until curl -sf "http://localhost:$port/health" >/dev/null 2>&1; do
        if ! kill -0 "$(cat "$LOG_DIR/$name.pid")" 2>/dev/null; then
            echo "!! $name process died during startup - last 50 log lines:" >&2
            tail -n 50 "$LOG_DIR/$name.log" >&2
            _diagnose_known_failures "$LOG_DIR/$name.log"
            exit 1
        fi
        if [ "$waited" -ge "$HEALTH_TIMEOUT_SECONDS" ]; then
            echo "!! $name did not become healthy within ${HEALTH_TIMEOUT_SECONDS}s - last 50 log lines:" >&2
            tail -n 50 "$LOG_DIR/$name.log" >&2
            _diagnose_known_failures "$LOG_DIR/$name.log"
            exit 1
        fi
        if [ $((waited % 60)) -eq 0 ] && [ "$waited" -gt 0 ]; then
            echo "   ... still waiting on $name (${waited}s elapsed) - last log line:"
            tail -n 1 "$LOG_DIR/$name.log"
        fi
        sleep 5
        waited=$((waited + 5))
    done
    echo "==> $name is healthy"
}

wait_for_health "planner" "$PLANNER_PORT"
wait_for_health "flash" "$FLASH_PORT"

cat <<EOF

==> Both endpoints are up and reporting /metrics:
    planner : http://0.0.0.0:${PLANNER_PORT}/v1  (served-model-name: ${PLANNER_SERVED_NAME})
    flash   : http://0.0.0.0:${FLASH_PORT}/v1  (served-model-name: ${FLASH_SERVED_NAME})
    logs    : ${LOG_DIR}/{planner,flash}.log
    pids    : ${LOG_DIR}/{planner,flash}.pid

Point LLM-Hell's model_endpoints at these two via 'manage.py add-endpoint'
using this pod's public host/port, and add both as Prometheus scrape
targets in prometheus/prometheus.yml (metrics_path: /metrics).
EOF
