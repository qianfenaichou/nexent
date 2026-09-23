#!/usr/bin/env bash
# Write-path benchmark suite for the kw_011 / session-B batching change.
#
# Design notes (why it is shaped this way):
#
#  * ONE sequential process, so no two timed passes overlap and contend for the
#    same server.
#  * A (batched, working tree) and B (legacy, git HEAD) are INTERLEAVED as
#    A,B,A,B,A,B. Running all A then all B would confound the comparison with
#    table growth over the run.
#  * The two tables are TRUNCATEd before every timed pass, so each measured
#    pass is a pure INSERT against an empty table - the same regime the
#    documented 79.4s baseline was taken in. Growth between passes cannot move
#    the numbers.
#  * Repeats > 1 in a single process (condition D) is measured separately: pass
#    1 inserts, passes 2+ take the lookup/update branch, which is a different
#    regime and is reported as such rather than averaged in.
#
# Usage:  source /home/qianqian/pg-env.sh && bash run_write_path_suite.sh
set -u

OUT_DIR="${BENCH_OUT_DIR:-/home/qianqian/bench-runs}"
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/suite.log"
cd "$NEXENT_ROOT" || exit 1

BENCH="competition/experiments/bench_write_path.py"
PY="$VENV_PY"
COMMON="--entities 20000 --edges 30000"

{
echo "=================================================================="
echo "write-path benchmark suite"
echo "started: $(date -Iseconds)"
echo "pg: $("$PGBIN/psql" -h 127.0.0.1 -p 5434 -U postgres -d nexent -tAc 'select version()')"
echo "max_stack_depth: $("$PGBIN/psql" -h 127.0.0.1 -p 5434 -U postgres -d nexent -tAc 'show max_stack_depth')"
echo "shared_buffers:  $("$PGBIN/psql" -h 127.0.0.1 -p 5434 -U postgres -d nexent -tAc 'show shared_buffers')"
echo "=================================================================="
} 2>&1 | tee -a "$LOG"

truncate_tables() {
    "$PGBIN/psql" -h 127.0.0.1 -p 5434 -U postgres -d nexent -q \
        -c "TRUNCATE nexent.kg_entity_t, nexent.kg_relation_t;" >/dev/null 2>&1
}

run() {                      # run <label> <extra args...>
    local label="$1"; shift
    {
        echo
        echo "=================================================================="
        echo "### ${label}"
        echo "### truncate; then: $(basename $PY) $(basename $BENCH) ${COMMON} $*"
        echo "=================================================================="
    } 2>&1 | tee -a "$LOG"
    truncate_tables
    "$PY" "$BENCH" $COMMON "$@" --output "$OUT_DIR/${label}.json" 2>&1 \
        | grep -v "^\[SQL:" | tee -a "$LOG"
    echo "### exit=${PIPESTATUS[0]}" 2>&1 | tee -a "$LOG"
}

# ---- A/B interleaved: the before-vs-after comparison the work order asks for
for i in 1 2 3; do
    run "A_batched_noidx_r${i}" --no-indexes --repeats 1 --batch-size 1000
    run "B_legacy_noidx_r${i}"  --no-indexes --repeats 1 --legacy
done

# ---- C: batched with the kw_011 indexes present (added write cost)
for i in 1 2 3; do
    run "C_batched_withidx_r${i}" --with-indexes --repeats 1 --batch-size 1000
done

# ---- D: repeats=3 in one process (insert then update/merge regime)
run "D_batched_noidx_repeats3" --no-indexes --repeats 3 --batch-size 1000
run "D_legacy_noidx_repeats3"  --no-indexes --repeats 3 --legacy

# ---- E: batch-size sweep (batched, indexes absent)
for K in 100 500 1000 5000 8000; do
    run "E_batched_K${K}" --no-indexes --repeats 1 --batch-size "$K"
done

{
echo
echo "=================================================================="
echo "finished: $(date -Iseconds)"
echo "raw JSON artifacts in $OUT_DIR"
echo "=================================================================="
} 2>&1 | tee -a "$LOG"
