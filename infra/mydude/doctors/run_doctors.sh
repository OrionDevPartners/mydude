#!/usr/bin/env bash
# MyDude Acceptance Doctors runner
# Usage:
#   ./run_doctors.sh                    # all checks
#   ./run_doctors.sh --static-only      # artifact analysis only
#   ./run_doctors.sh --check D01        # single check
#
# To enable live Azure checks, set:
#   export AZURE_SUBSCRIPTION_ID=<your-subscription-id>
#   export AZURE_TOKEN=$(az account get-access-token --query accessToken -o tsv)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Running MyDude Acceptance Doctors..."
echo "Working directory: $SCRIPT_DIR"
echo ""

if [ -n "${AZURE_SUBSCRIPTION_ID:-}" ] && [ -n "${AZURE_TOKEN:-}" ]; then
    echo "Azure credentials detected — live checks will run."
else
    echo "No AZURE_SUBSCRIPTION_ID/AZURE_TOKEN — static checks only."
    echo "To run live checks: export AZURE_SUBSCRIPTION_ID=<id> && export AZURE_TOKEN=\$(az account get-access-token --query accessToken -o tsv)"
fi

echo ""
python3 "${SCRIPT_DIR}/acceptance_doctors.py" "$@"
