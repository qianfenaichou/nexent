#!/usr/bin/env bash
# Batch-size sweep for the batched write path: find the knee, and find the ceiling.
#
# K=20000 is included on purpose: PostgreSQL allows at most 65535 bound
# parameters per statement, and a multi-VALUES INSERT costs one parameter per
# column per row. The sweep is expected to survive small K and fail at the top.
set -u
OUT_DIR="${BENCH_OUT_DIR:-/home/qianqian/bench-runs}"
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/sweep.log"
cd "$NEXENT_ROOT" || exit 1
BENCH="competition/experiments/bench_write_path.py"

{
echo "started: $(date -Iseconds)"
} 2>&1 | tee -a "$LOG"

# K is capped at 8000 on purpose: a single `(src,dst,rel_type) IN (...)` carrying
# more than ~8000-12000 tuples makes the PostgreSQL parser recurse past
# max_stack_depth and the whole write dies with
# StatementTooComplex: stack depth limit exceeded
# (measured: 8000 tuples OK, 12000 tuples fail, on PG 16.15 / max_stack_depth=2MB;
#  and a real run with batch_size=20000 reproduced the failure on the relation
#  lookup). K must therefore stay well below that ceiling.
for K in 100 500 1000 2000 4000 8000; do
    {
        echo
        echo "### K=$K"
    } 2>&1 | tee -a "$LOG"
    "$PGBIN/psql" -h 127.0.0.1 -p 5434 -U postgres -d nexent -q \
        -c "TRUNCATE nexent.kg_entity_t, nexent.kg_relation_t;" >/dev/null 2>&1
    "$VENV_PY" "$BENCH" --entities 20000 --edges 30000 --no-indexes --repeats 1 \
        --batch-size "$K" --output "$OUT_DIR/E_batched_K${K}.json" 2>&1 \
        | grep -v "^\[SQL:" \
        | grep -E "done in|indexes ABSENT|error|Error|written|###" | head -8 \
        | tee -a "$LOG"
    echo "### exit=${PIPESTATUS[0]}" | tee -a "$LOG"
done

{
echo
echo "finished: $(date -Iseconds)"
} 2>&1 | tee -a "$LOG"
