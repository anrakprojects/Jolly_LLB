#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Publish built update artifacts to Azure Blob Storage so installed Jolly LLB
# apps can auto-update (electron-updater `generic` feed).
#
# Run AFTER a build, e.g.:  npm run dist:mac && scripts/publish-azure.sh
#
# Required env:
#   AZURE_STORAGE_ACCOUNT     storage account name (e.g. anraklegaljolly)
# Optional env:
#   AZURE_STORAGE_CONTAINER   container name (default: jolly-updates)
#   one auth method:
#     AZURE_STORAGE_SAS_TOKEN   a SAS token with write perms, OR
#     AZURE_STORAGE_KEY         the account key, OR
#     (nothing) -> uses `az login` identity via --auth-mode login
#
# The container's PUBLIC feed URL must match the `publish.url` in package.json:
#   https://<AZURE_STORAGE_ACCOUNT>.blob.core.windows.net/<AZURE_STORAGE_CONTAINER>/
# Give the container "Blob (anonymous read)" access, or bake a read SAS into the
# publish.url so the app can fetch updates.
# ---------------------------------------------------------------------------
set -euo pipefail

: "${AZURE_STORAGE_ACCOUNT:?set AZURE_STORAGE_ACCOUNT (the storage account name)}"
AZURE_STORAGE_CONTAINER="${AZURE_STORAGE_CONTAINER:-jolly-updates}"
RELEASE_DIR="${1:-release}"

if ! command -v az >/dev/null 2>&1; then
  echo "error: Azure CLI ('az') not found. Install it: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "error: release dir '$RELEASE_DIR' not found — build first (npm run dist:mac)." >&2
  exit 1
fi

AUTH_ARGS=()
if [[ -n "${AZURE_STORAGE_SAS_TOKEN:-}" ]]; then
  AUTH_ARGS=(--sas-token "$AZURE_STORAGE_SAS_TOKEN")
elif [[ -n "${AZURE_STORAGE_KEY:-}" ]]; then
  AUTH_ARGS=(--account-key "$AZURE_STORAGE_KEY")
else
  AUTH_ARGS=(--auth-mode login)
fi

# electron-updater needs: the channel manifest(s) (*.yml) + the installers and
# their .blockmap deltas. We overwrite so re-publishing a version (and always
# the latest*.yml pointer) replaces the old blobs.
PATTERNS=("*.yml" "*.dmg" "*.zip" "*.exe" "*.msi" "*.blockmap" "*.AppImage" "*.deb" "*.rpm")

echo "Publishing from '$RELEASE_DIR' -> $AZURE_STORAGE_ACCOUNT/$AZURE_STORAGE_CONTAINER"
uploaded=0
for pat in "${PATTERNS[@]}"; do
  if compgen -G "$RELEASE_DIR/$pat" >/dev/null 2>&1; then
    echo "  uploading $pat ..."
    az storage blob upload-batch \
      --account-name "$AZURE_STORAGE_ACCOUNT" \
      --destination "$AZURE_STORAGE_CONTAINER" \
      --source "$RELEASE_DIR" \
      --pattern "$pat" \
      --overwrite true \
      "${AUTH_ARGS[@]}" >/dev/null
    uploaded=1
  fi
done

if [[ "$uploaded" -eq 0 ]]; then
  echo "warning: no matching artifacts found in '$RELEASE_DIR'." >&2
  exit 1
fi

echo "Done. Feed URL:"
echo "  https://$AZURE_STORAGE_ACCOUNT.blob.core.windows.net/$AZURE_STORAGE_CONTAINER/"
echo "Make sure package.json publish.url matches the line above."
