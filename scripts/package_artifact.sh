#!/usr/bin/env bash
#
# Build the artifact archive for Zenodo (or any permanent repository).
#
# The file set comes from git, never from a directory walk, so everything in
# .gitignore is excluded by construction. That matters here: the working
# directory also holds a 255 MB .venv, a 20 MB downloaded alloy.jar, an .idea/
# project directory, and nine symlinks created by scripts/link_pipeline.sh --
# none of which belong in a published artifact. `zip -r` would ship all of them.
#
# The virtual environment is deliberately NOT included: evaluators create it by
# running scripts/install.sh, which is the documented install path.
#
# Included but easy to lose: formal_verification/tools/tla2tools.jar, which is
# vendored on purpose because its upstream tag is a rolling prerelease. The
# script fails if it is missing.
#
# Usage:
#   scripts/package_artifact.sh                 archive the working tree
#   scripts/package_artifact.sh --from-head     archive the last commit instead
#   scripts/package_artifact.sh --flat          also emit docs+scripts as loose
#                                               files, for per-file Zenodo URLs
#   scripts/package_artifact.sh --out DIR       where to write (default: ./dist)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

OUT_DIR="${ROOT_DIR}/dist"
FROM_HEAD=0
FLAT=0
NAME="A2ABreak-artifact"

while [ $# -gt 0 ]; do
  case "$1" in
    --from-head) FROM_HEAD=1; shift ;;
    --flat)      FLAT=1; shift ;;
    --out)       OUT_DIR="$2"; shift 2 ;;
    -h|--help)   sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "${OUT_DIR}"
ZIP="${OUT_DIR}/${NAME}.zip"
LIST="$(mktemp)"
trap 'rm -f "${LIST}"' EXIT

# ------------------------------------------------------------------ file set
if [ "${FROM_HEAD}" = "1" ]; then
  echo "Source: last commit ($(git rev-parse --short HEAD))"
  git ls-tree -r --name-only HEAD > "${LIST}"
else
  echo "Source: working tree (tracked + untracked, honouring .gitignore)"
  git ls-files -co --exclude-standard > "${LIST}"
  UNCOMMITTED="$(git status --porcelain | wc -l | tr -d ' ')"
  if [ "${UNCOMMITTED}" != "0" ]; then
    echo "  note: ${UNCOMMITTED} path(s) differ from HEAD; the archive reflects the"
    echo "        working tree, which is usually what you want before committing."
  fi
fi

COUNT="$(wc -l < "${LIST}" | tr -d ' ')"
echo "  ${COUNT} files"

# ------------------------------------------------------------ safety checks
echo
echo "Checks:"
fail=0

must_be_absent() {
  local pattern="$1" label="$2"
  if grep -qE "${pattern}" "${LIST}"; then
    echo "  [FAIL] ${label} would be included"
    fail=1
  else
    echo "  [ ok ] ${label} excluded"
  fi
}

must_be_present() {
  local path="$1"
  if grep -qxF "${path}" "${LIST}"; then
    echo "  [ ok ] ${path}"
  else
    echo "  [FAIL] ${path} is MISSING from the archive"
    fail=1
  fi
}

must_be_absent '^\.venv/'                  '.venv/ (255 MB; evaluators build it via install.sh)'
must_be_absent '^\.idea/'                  '.idea/ (editor project files)'
must_be_absent 'formal_verification/tools/alloy\.jar' 'alloy.jar (downloaded and checksummed at run time)'
must_be_absent '^RFC_PSM_Benchmark/'       'RFC_PSM_Benchmark/ (third-party, cloned at a pinned commit)'
must_be_absent '__pycache__'               '__pycache__/'
must_be_absent '(^|/)\.DS_Store$'          '.DS_Store (macOS Finder metadata)'
must_be_absent '^(config\.yaml|stage_a1\.py|stage_a2\.py|stage_b1\.py|stage_b2\.py|merge_stage_a12\.py|verify_stage_a\.py|model_interface\.py|fetcher\.py)$' \
                                           'root symlinks from link_pipeline.sh'
must_be_absent '^\.env'                    '.env (API key)'

must_be_present 'formal_verification/tools/tla2tools.jar'
must_be_present 'metadata.toml'
must_be_present 'README.md'
must_be_present 'CLAIMS.md'
must_be_present 'REQUIREMENTS.md'
must_be_present 'INSTALL.md'
must_be_present 'PROVENANCE.md'
must_be_present 'ETHICS.md'
must_be_present 'LICENSE'
must_be_present 'scripts/install.sh'
must_be_present 'scripts/verify_claims.py'
must_be_present 'PSM_Benchmark/METHOD.md'
must_be_present 'outputs/final_verified.json'
must_be_present 'src/config.yaml'

if [ "${fail}" != "0" ]; then
  echo
  echo "Refusing to build the archive. Fix the failures above." >&2
  exit 1
fi

# ------------------------------------------------------------------- build
echo
echo "Building ${ZIP} ..."
rm -f "${ZIP}"
# -@ reads the file list from stdin; -X drops extended attributes and resource forks.
zip -q -X -@ "${ZIP}" < "${LIST}"

SIZE="$(du -h "${ZIP}" | awk '{print $1}')"
IN_ZIP="$(unzip -Z1 "${ZIP}" | wc -l | tr -d ' ')"
echo "  ${SIZE}, ${IN_ZIP} entries"

# Verify after the fact, not just before: prove .venv really is not in there.
if unzip -Z1 "${ZIP}" | grep -q '^\.venv/'; then
  echo "  [FAIL] .venv/ ended up in the archive" >&2
  exit 1
fi
echo "  [ ok ] verified: no .venv/ in the archive"

# -------------------------------------------------------------- flat layout
if [ "${FLAT}" = "1" ]; then
  FLAT_DIR="${OUT_DIR}/flat"
  echo
  echo "Emitting loose files into ${FLAT_DIR}"
  echo "  (for a Zenodo-only deposit, so metadata.toml's per-file URLs resolve"
  echo "   to https://zenodo.org/records/<id>/files/<name> instead of GitHub)"
  rm -rf "${FLAT_DIR}"; mkdir -p "${FLAT_DIR}"
  for f in README.md CLAIMS.md REQUIREMENTS.md INSTALL.md \
           PROVENANCE.md ETHICS.md metadata.toml LICENSE; do
    cp "${f}" "${FLAT_DIR}/"
  done
  cp scripts/*.sh scripts/*.py "${FLAT_DIR}/"
  echo "  $(ls -1 "${FLAT_DIR}" | wc -l | tr -d ' ') loose files"
  echo
  echo "  Upload these alongside ${NAME}.zip, then rewrite the 15 github.com"
  echo "  URLs in metadata.toml to their Zenodo file URLs."
fi

cat <<EOF

------------------------------------------------------------------
Archive ready: ${ZIP}

Before depositing:
  * unzip it somewhere clean and run  bash scripts/install.sh && bash scripts/smoke_test.sh
  * after minting the DOI, update artifact_url in metadata.toml and the
    citation field beside it
------------------------------------------------------------------
EOF
