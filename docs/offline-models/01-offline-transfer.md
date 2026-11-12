# 이미지·모델 반입과 GPU 확인

[목차](README.md)

## 외부망 준비

외부망 준비 PC에서 Linux x86-64용 이미지를 받습니다. 아래는 공식 Docker 이미지와 모델 지원표에서 함께 확인한 `v0.28.0` 고정 예시입니다. 반입할 릴리스의 CUDA와 폐쇄망 호스트 드라이버가 호환되는지 확인합니다. CUDA 13 이미지의 일반 구성은 R580 이상을 기준으로 확인하고, 드라이버 호환 모드는 별도 검증합니다. Docker와 NVIDIA Container Toolkit이 준비되지 않은 호스트는 해당 OS용 패키지 및 의존성을 내부 패키지 저장소로 반입합니다. [vLLM Docker](https://docs.vllm.ai/en/v0.28.0/deployment/docker/) · [vLLM-Omni Docker](https://docs.vllm.ai/projects/vllm-omni/en/v0.28.0/getting_started/installation/gpu/) · [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

가중치와 모델 tar가 함께 디스크를 차지합니다. 준비 PC와 폐쇄망 서버 양쪽에서 여유 용량을 확인합니다. 예제에서 사용하는 Bash, Python 3, `curl`, `jq`, `openssl`, `file`, GNU coreutils도 준비합니다. 다음 준비 명령은 같은 Bash 세션에서 실행합니다.

```bash
bundle_dir="$(mktemp -d "$PWD/llm-airgap-$(date +%Y%m%d)-XXXXXX")"
mkdir -p "$bundle_dir/models" "$bundle_dir/assets"
: > "$bundle_dir/model-revisions.tsv"

runtime_images=(vllm/vllm-openai:v0.28.0)
# 이미지 생성·편집이나 WAV 서비스를 올릴 때 주석을 풉니다.
# runtime_images+=(vllm/vllm-omni:v0.28.0)
# 검색 모델을 CPU에서 실행할 때 주석을 풉니다.
# runtime_images+=(vllm/vllm-openai-cpu:v0.28.0-x86_64)
# 기존 앱의 Qdrant Compose 옵션을 사용할 때 주석을 풉니다.
# runtime_images+=(qdrant/qdrant:v1.20.0)

for image in "${runtime_images[@]}"; do
  docker pull --platform linux/amd64 "$image" || exit 1
done
docker image inspect --format '{{json .RepoTags}} {{.Os}}/{{.Architecture}} {{.Id}} {{json .RepoDigests}}' \
  "${runtime_images[@]}" > "$bundle_dir/image-manifest.txt"

python3 -m venv .venv-llm-staging
source .venv-llm-staging/bin/activate
python -m pip install --upgrade huggingface_hub
```

접근 승인이 필요한 모델은 외부망에서 모델 카드의 이용 조건을 확인하고 필요한 접근을 받은 뒤 `hf auth login`으로 로그인합니다. Hugging Face 인증은 준비 PC에만 두고 반입물에는 모델 파일, 이미지 tar와 아래 명세만 넣습니다. [Hugging Face CLI](https://huggingface.co/docs/huggingface_hub/en/guides/cli)

이 함수는 모델 카드의 현재 커밋을 확인한 뒤 해당 리비전에서 다운로드하고, 완료한 ID·리비전·로컬 폴더를 기록합니다. 이미 검토한 리비전이 있다면 세 번째 인자로 지정합니다.

```bash
stage_model() {
  local repo_id="$1" folder="$2" revision="${3:-}"
  if [[ -z "$revision" ]]; then
    revision="$(python - "$repo_id" <<'PY'
import sys
from huggingface_hub import HfApi

print(HfApi().model_info(sys.argv[1]).sha)
PY
    )" || return 1
  fi
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || return 1
  hf download "$repo_id" --revision "$revision" --local-dir "$bundle_dir/models/$folder" || return 1
  printf '%s\t%s\t%s\n' "$repo_id" "$revision" "$folder" >> "$bundle_dir/model-revisions.tsv"
}

# 현재 HF 가중치가 없는 경우 주석을 풀어 실행합니다.
# stage_model google/gemma-4-31B-it gemma-4-31b-it

# 필요한 역할만 주석을 풀어 실행합니다.
# stage_model BAAI/bge-m3 bge-m3
# stage_model BAAI/bge-reranker-v2-m3 bge-reranker-v2-m3
# stage_model black-forest-labs/FLUX.1-schnell flux1-schnell
# stage_model black-forest-labs/FLUX.1-Kontext-dev flux1-kontext-dev
# stage_model black-forest-labs/FLUX.2-klein-4B flux2-klein-4b
# stage_model openbmb/VoxCPM2 voxcpm2
# stage_model stabilityai/stable-audio-open-1.0 stable-audio-open-1
```

기존 `google/gemma-4-31B-it`의 HF 체크포인트가 준비 PC에 있으면 다운로드 단계 대신 아래처럼 가져옵니다. `config.json`, 토크나이저, 이미지 프로세서 설정, 가중치 인덱스와 모든 샤드를 포함한 **모델 폴더 전체**가 필요합니다. HF 캐시의 스냅샷에 있는 심볼릭 링크는 `cp -aL`로 실제 파일을 복사합니다. 모델이 이미 폐쇄망 서버에 있다면 [기존 경로 사용](02-chat-and-vision.md#gemma-4-31b-실행)을 따르고 명세에 출처를 기록합니다.

```bash
existing_model=/path/to/approved/gemma-4-31B-it
test -f "$existing_model/config.json" || exit 1
mkdir -p "$bundle_dir/models/gemma-4-31b-it"
cp -aL "$existing_model"/. "$bundle_dir/models/gemma-4-31b-it/"
printf '%s\t%s\t%s\n' google/gemma-4-31B-it existing-approved-snapshot gemma-4-31b-it \
  >> "$bundle_dir/model-revisions.tsv"
```

Gemma의 도구 호출에 사용하는 Jinja 템플릿도 반입합니다. 실행 이미지와 같은 vLLM 태그의 파일을 저장합니다. [vLLM Gemma 4 템플릿](https://github.com/vllm-project/vllm/blob/v0.28.0/examples/tool_chat_template_gemma4.jinja)

```bash
curl --fail --location --retry 3 \
  https://raw.githubusercontent.com/vllm-project/vllm/v0.28.0/examples/tool_chat_template_gemma4.jinja \
  --output "$bundle_dir/assets/tool_chat_template_gemma4.jinja" || exit 1

docker image save -o "$bundle_dir/runtime-images.tar" "${runtime_images[@]}" || exit 1
tar --exclude=.cache -C "$bundle_dir" -cf "$bundle_dir/model-assets.tar" \
  models assets model-revisions.tsv || exit 1
(
  cd "$bundle_dir" || exit 1
  sha256sum runtime-images.tar model-assets.tar image-manifest.txt > SHA256SUMS
)
```

`runtime-images.tar`, `model-assets.tar`, `image-manifest.txt`, `SHA256SUMS`를 반입합니다. 모델 카드와 라이선스, 반입 승인 기록도 해당 버전에 맞춰 보관합니다. [Docker image save](https://docs.docker.com/reference/cli/docker/image/save/) · [Docker image load](https://docs.docker.com/reference/cli/docker/image/load/)

## 폐쇄망에서 가져오기

`bundle_in`을 반입물 경로로, `MODEL_DEPLOY_DIR`을 모델 운영 계정이 쓸 수 있는 절대 경로로 바꿉니다. 기존 운영 중인 모델은 별도 경로에서 유지하고 새 버전은 새 디렉터리에 풉니다.

```bash
bundle_in=/mnt/import/llm-airgap-bundle
export MODEL_DEPLOY_DIR=/srv/llm-offline/v1
export VLLM_IMAGE=vllm/vllm-openai:v0.28.0
export OMNI_IMAGE=vllm/vllm-omni:v0.28.0
export VLLM_CPU_IMAGE=vllm/vllm-openai-cpu:v0.28.0-x86_64

(cd "$bundle_in" && sha256sum -c SHA256SUMS) || exit 1
docker image load -i "$bundle_in/runtime-images.tar" || exit 1
mkdir -p "$MODEL_DEPLOY_DIR" || exit 1
tar -xf "$bundle_in/model-assets.tar" -C "$MODEL_DEPLOY_DIR" || exit 1

uname -m
docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Id}}' "$VLLM_IMAGE"
nvidia-smi --query-gpu=index,uuid,name,driver_version,memory.total,memory.used --format=csv
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
nvidia-smi topo -m

# 0,1은 Gemma에 할당할 GPU 두 장의 번호 또는 UUID로 바꿉니다.
docker run --rm --pull=never --network none --gpus '"device=0,1"' \
  --entrypoint nvidia-smi "$VLLM_IMAGE"
```

GPU 확인 컨테이너에서 `nvidia-smi`가 실패하면 호스트 드라이버와 Container Toolkit을 먼저 점검합니다. Toolkit을 새로 설정하는 경우 `nvidia-ctk runtime configure --runtime=docker` 적용과 Docker 데몬 재시작이 필요할 수 있으므로 기존 컨테이너의 운영 일정에 맞춥니다. [NVIDIA Docker 런타임 설정](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuring-docker)

## 공통 실행 명령

다음 블록을 실행한 Bash 세션에서 이후 `serve_model`과 `model_curl` 명령을 사용합니다. 다른 세션에서 사용하려면 변수와 함수 정의를 다시 불러옵니다. 기본은 호스트 루프백 바인딩과 앱 전용 Docker 네트워크입니다. 다른 서버에서 모델 API를 호출해야 할 때만 [사설망 연결 방법](06-app-connection.md#서로-다른-호스트에서-연결)을 적용합니다.

```bash
set -o pipefail
export MODEL_NETWORK=llm-models
export MODEL_BIND=127.0.0.1
export MODEL_KEY_FILE="$MODEL_DEPLOY_DIR/vllm-api.env"

if [[ ! -s "$MODEL_KEY_FILE" ]]; then
  (umask 077; printf 'VLLM_API_KEY=%s\n' "$(openssl rand -hex 32)" > "$MODEL_KEY_FILE")
fi
MODEL_API_KEY="$(sed -n 's/^VLLM_API_KEY=//p' "$MODEL_KEY_FILE")"
[[ -n "$MODEL_API_KEY" ]] || exit 1
docker network inspect "$MODEL_NETWORK" >/dev/null 2>&1 || docker network create "$MODEL_NETWORK"

serve_model() {
  local container="$1" device="$2" host_port="$3" runtime_image="$4" model_path="$5"
  shift 5
  local weights_dir="${MODEL_WEIGHTS_DIR:-$MODEL_DEPLOY_DIR$model_path}"
  if [[ ! -d "$weights_dir" ]]; then
    printf '모델 폴더가 없습니다. %s\n' "$weights_dir" >&2
    return 1
  fi
  local gpu_option=()
  if [[ "$device" != cpu ]]; then
    gpu_option=(--gpus "\"device=$device\"")
  fi

  docker run -d --pull=never --restart unless-stopped \
    --name "$container" --network "$MODEL_NETWORK" "${gpu_option[@]}" --shm-size=8g \
    --mount "type=bind,source=$weights_dir,target=$model_path,readonly" \
    --mount "type=bind,source=$MODEL_DEPLOY_DIR/assets,target=/offline-assets,readonly" \
    --env-file "$MODEL_KEY_FILE" \
    -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 \
    -e VLLM_NO_USAGE_STATS=1 \
    -p "$MODEL_BIND:$host_port:8000" \
    --entrypoint vllm "$runtime_image" serve "$model_path" "$@" --host 0.0.0.0 --port 8000
}

model_curl() {
  curl --fail --silent --show-error --connect-timeout 5 --max-time 240 \
    -H "Authorization: Bearer $MODEL_API_KEY" "$@"
}
```

모델마다 반입한 로컬 경로를 인자로 넘깁니다. `--pull=never`는 이미지 자동 다운로드를 막고, 오프라인 환경변수는 런타임의 Hugging Face 조회를 제한합니다. `VLLM_API_KEY`는 해당 API 경로의 인증에 사용합니다. Docker 네트워크와 방화벽으로 관리·디버그 경로까지 접근 범위를 제한합니다. [vLLM 환경변수](https://docs.vllm.ai/en/v0.28.0/configuration/env_vars/) · [vLLM 인증 범위](https://docs.vllm.ai/en/v0.28.0/usage/security/) · [Docker 포트 바인딩](https://docs.docker.com/engine/network/port-publishing/)

폐쇄망 반입 전에 같은 GPU 환경에서 이 플래그와 로컬 경로로 서버를 시작하고, 각 문서의 API 시험을 실행합니다. 사내 방화벽에서 외부 통신이 차단된 환경으로도 한 번 실행하면 모델이 참조하는 토크나이저·보조 가중치 누락을 찾을 수 있습니다.
