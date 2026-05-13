#!/bin/bash

# This is almost a copy-version of
# https://github.com/confidential-containers/cloud-api-adaptor/blob/main/src/cloud-api-adaptor/hack/verify-provenance.sh

# Verify GitHub's attestation reports. Meant to verify binaries built
# by upstream projects (kata-containers and guest-components).
#
# GH cli is used to verify.
#
# Asserts on the claims are:
# - GitHub workflow trigger matches the value given with -t (expected_workflow_trigger),
#   e.g. workflow_dispatch or push — not assumed to be push
# - GitHub workflow ref is refs/heads/main (hard-coded; not configurable)
# - Built on the given repository
# - The gh action workflow is matching the given digest
# - The code is matching the given digest
#
# -g will fetch attestation via gh cli, this requires GH_TOKEN to be
# set. By default the attestation will be retrieved by walking the OCI
# manifest

set -euo pipefail

usage() {
	echo "Usage: $0 "
	echo "  -a <oci-artifact w/ sha256 digest>"
	echo "  -s <expected source repository revision from SLSA provenance>"
	echo "  -w <expected github workflow digest>"
	echo "  -t <expected github workflow trigger>"
	echo "  -r <repository on which the artifact was built>"
	echo "  [-g] (optional. fetch attestation using github api)"
	exit 1
}

oci_artifact=""
expected_source_revision=""
expected_workflow_digest=""
expected_workflow_trigger=""
repository=""
github="0"

# Parse options using getopts
while getopts ":a:s:w:t:r:g" opt; do
	case "${opt}" in
	a)
		oci_artifact="${OPTARG}"
		;;
	s)
		expected_source_revision="${OPTARG}"
		;;
	w)
		expected_workflow_digest="${OPTARG}"
		;;
	t)
		expected_workflow_trigger="${OPTARG}"
		;;
	r)
		repository="${OPTARG}"
		;;
	g)
		github="1"
		;;
	*)
		usage
		;;
	esac
done

# Check if all required arguments are provided
if [ -z "${oci_artifact}" ] || [ -z "${expected_source_revision}" ] || [ -z "${expected_workflow_digest}" ] || [ -z "${expected_workflow_trigger}" ] || [ -z "${repository}" ]; then
	usage
fi

if ! [[ "$oci_artifact" =~ @sha256:[a-fA-F0-9]{64}$ ]]; then
	echo "The OCI artifact should be specified using its digest: my-repo.io/my-image@sha256:abc..."
	exit 1
fi

# Convention by gh cli; set before trap so cleanup never sees an unbound variable under set -u.
attestation_bundle="${oci_artifact#*@}.jsonl"

cleanup() {
    rm -f "$attestation_bundle"
}
trap cleanup EXIT SIGINT SIGTERM

if [ "$github" != "1" ]; then
	attestation_manifest_digest=$(oras discover "$oci_artifact" --format json | jq -r '
		[(.referrers // [])[]
		| select(.artifactType | test("sigstore.bundle.*json"))
		| .digest][0] // empty
	')

	# If referrers discovery does not return a bundle digest (layout/permissions/compat
	# differences), switch to GitHub API mode when GH_TOKEN is available; the download runs
	# once in the shared if/else block below (same as -g).
	if [ -z "$attestation_manifest_digest" ]; then
		if [ -n "${GH_TOKEN:-}" ] || [ -n "${GITHUB_TOKEN:-}" ]; then
			echo "No attestation referrer found via oras discover; falling back to gh attestation download"
			github="1"
		else
			echo "Failed to discover attestation manifest digest for ${oci_artifact}"
			echo "Hint: provide GH_TOKEN/GITHUB_TOKEN and use -g to fetch bundle via GitHub API"
			exit 1
		fi
	fi

	# Fallback above may switch github=1; skip ORAS-manifest logic in that case.
	if [ "$github" != "1" ]; then
		oci_base="${oci_artifact%@*}"
		attestation_manifest="${oci_base}@${attestation_manifest_digest}"

		# One manifest may list multiple layers whose mediaType matches sigstore.bundle.*json.
		# Collect digests as JSON array so we never pass newline-separated values into an OCI ref.
		# If multiple match, use the first deterministically and log once.
		attestation_manifest_json=$(oras manifest fetch "$attestation_manifest" --format json)
		bundle_layer_digests_json=$(echo "$attestation_manifest_json" | jq -c '
			[(.layers // .content.layers // [])[]
			| select(.mediaType | test("sigstore.bundle.*json"))
			| .digest]
		')
		bundle_layer_count=$(echo "$bundle_layer_digests_json" | jq 'length')

		if [ "$bundle_layer_count" -eq 0 ]; then
			echo "No sigstore bundle JSON layer in attestation manifest ${attestation_manifest}: expected .layers[] or .content.layers[] with mediaType matching sigstore.bundle.*json"
			exit 1
		fi

		attestation_bundle_digest=$(echo "$bundle_layer_digests_json" | jq -r '.[0]')

		if [ "$bundle_layer_count" -gt 1 ]; then
			echo "Note: ${bundle_layer_count} layers match sigstore.bundle.*json in ${attestation_manifest}; using first digest: ${attestation_bundle_digest}"
		fi

		attestation_image="${oci_base}@${attestation_bundle_digest}"

		oras blob fetch --no-tty "$attestation_image" --output "$attestation_bundle"
	fi
fi

if [ "$github" = "1" ]; then
	gh attestation download "oci://${oci_artifact}" -R "$repository"
fi

claims=$(
	gh attestation verify "oci://${oci_artifact}" \
		-b "$attestation_bundle" \
		-R "$repository" \
		--format json \
		-q '[.[].verificationResult.signature.certificate
		| {
			digest:          .sourceRepositoryDigest,
			workflowDigest:  .githubWorkflowSHA,
			workflowTrigger: .githubWorkflowTrigger,
			workflowRef:     .githubWorkflowRef,
		}]'
)

# gh attestation verify may return multiple verification results. Treat verification
# as successful when any one attestation matches all expected claims.
matching_claims_count=$(echo "$claims" | jq -r \
	--arg expected_source_revision "$expected_source_revision" \
	--arg expected_workflow_digest "$expected_workflow_digest" \
	--arg expected_workflow_trigger "$expected_workflow_trigger" \
	'[.[] | select(
		.digest == $expected_source_revision and
		.workflowDigest == $expected_workflow_digest and
		.workflowTrigger == $expected_workflow_trigger and
		.workflowRef == "refs/heads/main"
	)] | length')

if [ "$matching_claims_count" -eq 0 ]; then
	echo "Verification failed: no attestation matched all expected claims"
	echo "Expected source digest: $expected_source_revision"
	echo "Expected workflow digest: $expected_workflow_digest"
	echo "Expected workflow trigger: $expected_workflow_trigger"
	echo "Expected workflow ref: refs/heads/main"

	# Print observed values to make triage easier when multiple attestations exist.
	echo "Observed source digests: $(echo "$claims" | jq -r '[.[].digest] | unique | join(", ")')"
	echo "Observed workflow digests: $(echo "$claims" | jq -r '[.[].workflowDigest] | unique | join(", ")')"
	echo "Observed workflow triggers: $(echo "$claims" | jq -r '[.[].workflowTrigger] | unique | join(", ")')"
	echo "Observed workflow refs: $(echo "$claims" | jq -r '[.[].workflowRef] | unique | join(", ")')"
	exit 1
fi

echo "Verification passed"