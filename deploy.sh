#!/bin/bash
# Build & push the nanobot image from the Jenkins toolchain (in-container
# compile — the agent only needs Docker), then deploy to EKS via helm,
# following the visla-api deploy.sh conventions: git-<sha> immutable tags,
# region-selected ECR, idempotent.
#
# Usage:
#   deploy.sh <region> <env>
#
# region: cn-* → cn-northwest-1 ECR, us-* → us-west-2 ECR
# env:    dev-[0-9]+ | prod-[0-9]+ | test* (values file: values-${region}-${env}.yaml)

set -u

name=nanobot
usage="Usage: deploy.sh <region> <env>"

region=$1
env=$2

version="git-$(git rev-parse HEAD 2>/dev/null)"
[ -n "${version#git-}" ] || { echo "not a git worktree — cannot derive version"; exit 1; }

if [[ "$region" =~ ^cn-.+$ ]]; then
  ecr=665444435332.dkr.ecr.cn-northwest-1.amazonaws.com.cn
elif [[ "$region" =~ ^us-.+$ ]]; then
  ecr=820814109514.dkr.ecr.us-west-2.amazonaws.com
else
  echo "$usage"; exit 1
fi

if [[ ! "$env" =~ ^(dev|prod)-[0-9]+$ || "$env" =~ ^test.* ]]; then
  :  # proceed; the helm chart files define which envs are valid
fi

exit_on_error() {
  if [ "$1" -ne 0 ]; then
    echo "Command failed with exit code $1."
    exit "$1"
  fi
}

# Reuse the per-environment AWS profile when present (mirrors visla-api).
aws_profile=""
if aws configure --profile "${region}-${env}" list >/dev/null 2>&1; then
  aws_profile="--profile ${region}-${env}"
fi

# Log into AWS ECR.
echo "Log into $ecr"
aws ecr get-login-password --region "$region" $aws_profile | \
  docker login --username AWS --password-stdin "$ecr"
exit_on_error $?

# Build only when the tag is neither on ECR nor local (immutable tags).
if aws ecr describe-images --region "$region" --repository-name "$name" \
     --image-ids=imageTag="$version" $aws_profile >/dev/null 2>&1; then
  echo "${name}:${version} already exists on ECR"
else
  if docker image inspect "${name}:${version}" >/dev/null 2>&1; then
    echo "${name}:${version} already exists locally"
  else
    echo "Building ${name}:${version} (in-container toolchain, Jenkins_Dockerfile)"
    docker build --label "builder=$(git config user.email)" \
      -t "${name}:${version}" -f Jenkins_Dockerfile .
    exit_on_error $?
  fi
  docker tag "${name}:${version}" "${ecr}/${name}:${version}" \
    && docker push "${ecr}/${name}:${version}"
  exit_on_error $?
fi

# Deploy to EKS via the chart (mirrors visla-api deploy.sh tail).
helm_values=helm-chart/${name}/values-${region}-${env}.yaml
kube_context=eks_${region}-${env}
[ -f "$helm_values" ] || { echo "values file missing: $helm_values"; exit 1; }

echo "Deploying ${ecr}/${name}:${version} → ${kube_context}"
helm template helm-chart/${name}/. -f "$helm_values" \
  --set-string image.tag="${version}" | \
  kubectl apply -f - --context "$kube_context"
exit_on_error $?
