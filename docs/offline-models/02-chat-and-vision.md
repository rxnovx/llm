# 채팅 LLM과 첨부 이미지 분석

[목차](README.md) · [공통 실행 명령](01-offline-transfer.md#공통-실행-명령)

## Gemma 4 31B 실행

`google/gemma-4-31B-it`은 텍스트와 이미지를 입력받고 텍스트를 출력합니다. 채팅·도구 호출·첨부 이미지 분석을 하나의 vLLM 연결로 사용합니다. BF16 가중치는 공식 예시에 맞춰 H100 두 장에 나눠 올립니다. 예제는 문맥 16K와 동시 시퀀스 4개로 시작합니다. 10명 동시 사용 성능은 실제 입력 길이와 요청 빈도로 측정합니다. [Google 모델 카드](https://huggingface.co/google/gemma-4-31B-it) · [vLLM H100 배치 예시](https://recipes.vllm.ai/Google/gemma-4-31B-it) · [Gemma 도구 호출](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html#tool-calling)

```bash
# 비어 있는 GPU 두 장의 번호 또는 UUID를 쉼표로 연결합니다.
gemma_gpus=0,1
# 기존 HF 모델이 폐쇄망에 있으면 이 값을 config.json이 있는 절대 경로로 바꿉니다.
gemma_weights="$MODEL_DEPLOY_DIR/models/gemma-4-31b-it"
MODEL_WEIGHTS_DIR="$gemma_weights" serve_model llm-chat "$gemma_gpus" 50301 "$VLLM_IMAGE" /models/gemma-4-31b-it \
  --served-model-name gemma-4-31b-it \
  --dtype bfloat16 --tensor-parallel-size 2 --max-model-len 16384 \
  --max-num-seqs 4 --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 \
  --chat-template /offline-assets/tool_chat_template_gemma4.jinja \
  --limit-mm-per-prompt '{"image":4}'
```

`--served-model-name`이 앱에서 사용할 ID입니다. `docker logs -f llm-chat`에서 가중치 로딩이 끝날 때까지 기다립니다. 이어서 등록된 ID와 스트리밍을 확인합니다. 스트리밍 시험에서는 답변 토큰과 종료 이벤트 `[DONE]`이 표시됩니다.

```bash
chat_api="http://$MODEL_BIND:50301/v1"
model_curl "$chat_api/models" | jq -e '.data[] | select(.id == "gemma-4-31b-it")'
model_curl -N "$chat_api/chat/completions" -H 'Content-Type: application/json' -d '{
  "model":"gemma-4-31b-it","messages":[{"role":"user","content":"한국어로 한 문장 인사해 줘."}],
  "stream":true,"max_tokens":512
}'
```

PPTX와 같은 파일 생성은 `tool_calls`를 사용합니다. 이 시험은 외부 호출 없이 모델이 함수 이름을 응답에 넣는지 확인합니다.

```bash
model_curl "$chat_api/chat/completions" -H 'Content-Type: application/json' -d '{
  "model":"gemma-4-31b-it",
  "messages":[{"role":"user","content":"read_test_status 도구를 한 번 호출해서 테스트 상태를 확인해 줘."}],
  "tools":[{"type":"function","function":{"name":"read_test_status","description":"Returns the offline deployment test status","parameters":{"type":"object","properties":{},"additionalProperties":false}}}],
  "tool_choice":"auto","max_tokens":768
}' | jq -e '.choices[0].message.tool_calls[0].function.name == "read_test_status"'
```

자동 이미지 처리 경로를 사용하려면 구조화 응답도 확인합니다.

```bash
model_curl "$chat_api/chat/completions" -H 'Content-Type: application/json' -d '{
  "model":"gemma-4-31b-it",
  "messages":[{"role":"user","content":"Set status to ready."}],
  "response_format":{"type":"json_schema","json_schema":{"name":"offline_check","strict":true,"schema":{"type":"object","properties":{"status":{"type":"string","enum":["ready"]}},"required":["status"],"additionalProperties":false}}},
  "max_tokens":768
}' | jq -e '.choices[0].message.content | fromjson | .status == "ready"'
```

## 같은 서버에서 이미지 입력 확인

`vision_sample`을 테스트용 PNG 경로로 바꿉니다. 동일한 `50301` 서버에 PNG를 JSON으로 보냅니다. 시험 후 결과가 실제 이미지와 맞는지 확인합니다.

```bash
vision_sample=/path/to/test-screen.png
python3 - "$vision_sample" <<'PY' | model_curl "$chat_api/chat/completions" \
  -H 'Content-Type: application/json' --data-binary @- | jq -er '.choices[0].message.content | select(length > 0)'
import base64
import json
from pathlib import Path
import sys

image = base64.b64encode(Path(sys.argv[1]).read_bytes()).decode("ascii")
print(json.dumps({
    "model": "gemma-4-31b-it",
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "이 화면에서 보이는 내용을 한국어로 한 문장 설명해 줘."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}},
    ]}],
    "max_tokens": 512,
}, ensure_ascii=False))
PY
```

앱 설정에서 같은 연결에 `supports_tools=true`와 `supports_vision=true`를 지정합니다. `context_window`는 vLLM의 `--max-model-len`과 같거나 작게 둡니다. [앱 모델 등록](06-app-connection.md#앱-설정)
