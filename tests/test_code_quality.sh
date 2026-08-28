#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ALLOWED_IGNORES=(
    COM812
    ISC001
    C901
    D203
    D213
    PLR0911
    PLR0912
    PLR0915
    PLR2004
    D107
    S311
    SIM117
    FIX002
    TD002
    TD003
    CPY001
)

# The per-file exceptions the maintainer has sanctioned, as recorded next to
# them in pyproject.toml. Entries are "pattern" for the table key and
# "pattern -> CODE" for each rule under it; anything not listed here is new
# and gets flagged. Kept as a baseline rather than an outright ban so the
# check stays useful: the point is to catch *new* escape hatches.
ALLOWED_PER_FILE=(
    "tests/**"
    "tests/** -> S101"
)

is_allowed_per_file() {
    local entry=$1
    local allowed
    for allowed in "${ALLOWED_PER_FILE[@]}"; do
        if [[ "$allowed" == "$entry" ]]; then
            return 0
        fi
    done
    return 1
}

is_allowed_ignore() {
    local code=$1
    local allowed
    for allowed in "${ALLOWED_IGNORES[@]}"; do
        if [[ "$allowed" == "$code" ]]; then
            return 0
        fi
    done
    return 1
}

check_noqa_comments() {
    local violations=()
    local py_file relative_path match line_num line

    while IFS= read -r -d '' py_file; do
        relative_path=${py_file#"$ROOT_DIR"/}
        while IFS= read -r match; do
            line_num=${match%%:*}
            line=${match#*:}
            violations+=("$relative_path:$line_num: $line")
        done < <(grep -inE '#[[:space:]]*noqa' "$py_file" || true)
    done < <(
        find "$ROOT_DIR" \
            \( -type d \( -name .venv -o -name .git -o -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name .ruff_cache \) -prune \) \
            -o -type f -name '*.py' -print0
    )

    if ((${#violations[@]} > 0)); then
        printf '%s\n' 'noqa comments found (not allowed):'
        printf '  %s\n' "${violations[@]}"
        return 1
    fi
}

check_ruff_ignores() {
    local violations=()
    local code pattern
    local per_file_records=()
    local record record_type record_pattern record_code

    while IFS= read -r code; do
        [[ -z "$code" ]] && continue
        if ! is_allowed_ignore "$code"; then
            violations+=("new top-level ignore not in baseline: $code")
        fi
    done < <(
        awk '
            /^\[tool\.ruff\.lint\]$/ { in_lint=1; next }
            /^\[/ { in_lint=0; in_ignore=0 }
            in_lint && /^[[:space:]]*ignore[[:space:]]*=/ { in_ignore=1 }
            in_ignore {
                line=$0
                sub(/[[:space:]]*#.*/, "", line)
                while (match(line, /"[^"]+"/)) {
                    print substr(line, RSTART + 1, RLENGTH - 2)
                    line=substr(line, RSTART + RLENGTH)
                }
            }
            in_ignore && /\]/ { in_ignore=0 }
        ' pyproject.toml
    )

    while IFS=$'\t' read -r record_type pattern code; do
        [[ -z "$record_type" ]] && continue
        per_file_records+=("$record_type"$'\t'"$pattern"$'\t'"$code")
    done < <(
        awk '
            /^\[tool\.ruff\.lint\.per-file-ignores\]$/ { in_per_file=1; next }
            /^\[/ { in_per_file=0 }
            in_per_file && /=/ {
                line=$0
                sub(/[[:space:]]*#.*/, "", line)
                if (match(line, /"[^"]+"/)) {
                    pattern=substr(line, RSTART + 1, RLENGTH - 2)
                    rest=substr(line, RSTART + RLENGTH)
                    print "P\t" pattern "\t"
                    while (match(rest, /"[^"]+"/)) {
                        code=substr(rest, RSTART + 1, RLENGTH - 2)
                        print "C\t" pattern "\t" code
                        rest=substr(rest, RSTART + RLENGTH)
                    }
                }
            }
        ' pyproject.toml
    )

    for record in "${per_file_records[@]}"; do
        IFS=$'\t' read -r record_type pattern record_code <<< "$record"
        if [[ "$record_type" == C ]] \
            && ! is_allowed_per_file "$pattern -> $record_code"; then
            violations+=("new per-file-ignore not in baseline: $pattern -> $record_code")
        fi
    done

    for record in "${per_file_records[@]}"; do
        IFS=$'\t' read -r record_type pattern record_code <<< "$record"
        if [[ "$record_type" == P ]] && ! is_allowed_per_file "$pattern"; then
            violations+=("new per-file-ignores entry not in baseline: $pattern")
        fi
    done

    if ((${#violations[@]} > 0)); then
        printf '%s\n' 'new ruff ignore exceptions found:'
        printf '  %s\n' "${violations[@]}"
        return 1
    fi
}

check_noqa_comments
check_ruff_ignores
