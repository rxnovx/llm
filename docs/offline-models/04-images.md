# 이미지 생성과 편집

[목차](README.md) · [공통 실행 명령](01-offline-transfer.md#공통-실행-명령)

앱은 생성에 JSON `prompt`, 편집에 multipart의 `image`와 `prompt`를 보냅니다. 두 요청 모두 이미지 한 장을 `data[0].b64_json`으로 받아 저장합니다. 모델 서버에서 PNG·JPEG·WebP 중 하나를 반환해야 하며 앱 파일 한도는 10MiB, 이미지 한도는 1,600만 픽셀입니다. [생성 API](https://docs.vllm.ai/projects/vllm-omni/en/stable/serving/image_generation_api/) · [편집 API](https://docs.vllm.ai/projects/vllm-omni/en/stable/serving/image_edit_api/)

## 생성과 편집을 분리하는 구성

텍스트→이미지에는 Apache 2.0의 `FLUX.1-schnell`을 사용합니다. 편집이 필요하면 별도의 `FLUX.1-Kontext-dev`를 배치할 수 있습니다. Kontext의 가중치는 비상업용 라이선스입니다. 사내 운영에 사용할 때는 조직이 해당 용도의 가중치 사용 권한을 확인한 경우에만 배치합니다. [Schnell 모델 카드](https://huggingface.co/black-forest-labs/FLUX.1-schnell) · [Kontext 모델 카드와 조건](https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev)

```bash
image_gpu=2
serve_model llm-image "$image_gpu" 50305 "$OMNI_IMAGE" /models/flux1-schnell --omni

# 편집을 함께 띄울 때는 edit_gpu에 VRAM을 확인한 GPU 번호를 지정합니다.
# 같은 GPU라면 생성과 편집을 각각 단독으로 시험합니다.
edit_gpu=2
# serve_model llm-image-edit "$edit_gpu" 50306 "$OMNI_IMAGE" /models/flux1-kontext-dev --omni
```

생성은 `http://$MODEL_BIND:50305/v1/images/generations`, 편집은 `http://$MODEL_BIND:50306/v1/images/edits`로 시험합니다. 편집 서버를 준비하기 전에는 앱의 `IMAGE_EDIT_URL`을 비워 둡니다.

## 한 모델에서 생성과 편집

`FLUX.2-klein-4B`의 4B 가중치는 Apache 2.0입니다. 생성과 참조 이미지 편집을 같은 체크포인트에서 처리하며 내부 텍스트 인코더에 Qwen3를 사용합니다. 조직의 반입 범위에 맞으면 생성·편집 컨테이너 두 개를 이 구성 하나로 대체합니다. [Klein 4B 모델 카드](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) · [텍스트 인코더 설정](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/blob/main/text_encoder/config.json) · [vLLM-Omni Klein 파이프라인](https://docs.vllm.ai/projects/vllm-omni/en/v0.28.0/api/vllm_omni/diffusion/models/flux2_klein/)

```bash
image_gpu=2
serve_model llm-image "$image_gpu" 50305 "$OMNI_IMAGE" /models/flux2-klein-4b --omni
```

이 구성에서는 생성과 편집에 모두 포트 `50305`를 사용합니다. 앱의 `IMAGE_GENERATION_URL`과 `IMAGE_EDIT_URL`에는 각각의 API 경로까지 포함한 주소를 입력합니다. 모델 ID는 각 서버의 기본값을 사용하도록 비워 둘 수 있습니다.

## 결과 바이트 확인

아래 요청은 앱과 같은 응답 형식을 확인합니다. `edit_port`는 분리 구성에서 `50306`, Klein 구성에서 `50305`입니다. 생성 시험에 성공하면 방금 받은 PNG를 편집 요청에 사용할 수 있습니다.

```bash
model_curl "http://$MODEL_BIND:50305/v1/images/generations" \
  -H 'Content-Type: application/json' -d '{
    "prompt":"A blue mug on a wooden table, simple product photo",
    "n":1,"response_format":"b64_json"
  }' | jq -er '.data | select(length == 1) | .[0].b64_json | strings | select(length > 0)' \
  | base64 --decode > offline-generation.png
file offline-generation.png

edit_port=50306
model_curl "http://$MODEL_BIND:$edit_port/v1/images/edits" \
  -F 'image=@offline-generation.png' \
  -F 'prompt=Change the mug color to green. Keep the composition.' \
  -F 'n=1' -F 'response_format=b64_json' \
  | jq -er '.data | select(length == 1) | .[0].b64_json | strings | select(length > 0)' \
  | base64 --decode > offline-edit.png
file offline-edit.png
```

두 파일이 이미지로 열리고 편집 요청의 색이 반영되는지 확인합니다. 앱에서 **모델 관리 → 이미지 서버 진단**으로 한 번 더 확인한 뒤, 대화에서 생성과 직전 이미지 편집을 각각 시험합니다. [앱 이미지 연결 설정](06-app-connection.md#앱-설정)
