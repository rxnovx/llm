# 임베딩과 검색 결과 재정렬

[목차](README.md) · [공통 실행 명령](01-offline-transfer.md#공통-실행-명령)

기본 SQLite 키워드 검색만으로 Office·PDF를 읽고 검색할 수 있습니다. 문서에 같은 단어가 없어도 의미가 비슷한 구간을 찾으려면 임베딩을 연결합니다. 임베딩을 쓰면서 벡터를 SQLite에 둘 수 있고, Qdrant는 별도 벡터 저장소가 필요할 때 선택합니다. [앱 검색 구조와 Qdrant](../../ASSISTANT.md#내부-임베딩qdrant재정렬)

## BGE-M3 임베딩

`BAAI/bge-m3`는 다국어 모델이며 MIT 라이선스로 공개되어 있습니다. 앱에서 사용하는 입력은 텍스트 배열, 출력은 문장별 dense 벡터입니다. vLLM의 일반 pooling 러너로 제공합니다. [BGE-M3 모델 카드](https://huggingface.co/BAAI/bge-m3) · [vLLM BGE-M3 지원](https://docs.vllm.ai/en/v0.28.0/models/pooling_models/specific_models/#baai-bge-m3)

```bash
# 여유 GPU를 사용할 때 이 블록을 실행합니다.
embedding_gpu=2
serve_model llm-embedding "$embedding_gpu" 50303 "$VLLM_IMAGE" /models/bge-m3 \
  --served-model-name bge-m3 --runner pooling \
  --max-model-len 8192 --max-num-seqs 4 --gpu-memory-utilization 0.20
```

GPU를 채팅·이미지에 우선 배정하려면 반입 단계에서 CPU 이미지를 추가하고 아래 명령을 사용합니다. GPU 예제와 같은 서비스명이므로 하나만 실행합니다. CPU의 명령어 지원과 응답 시간은 실제 호스트에서 확인합니다. [vLLM CPU 이미지와 요구사항](https://docs.vllm.ai/en/v0.28.0/getting_started/installation/cpu/)

```bash
serve_model llm-embedding cpu 50303 "$VLLM_CPU_IMAGE" /models/bge-m3 \
  --served-model-name bge-m3 --runner pooling --dtype float32 \
  --max-model-len 8192 --max-num-seqs 4
```

두 한국어 입력에 대해 각각 1,024차원 벡터가 반환되는지 확인합니다.

```bash
model_curl "http://$MODEL_BIND:50303/v1/embeddings" -H 'Content-Type: application/json' -d '{
  "model":"bge-m3","input":["연차 신청은 사내 포털에서 합니다.","문서 반출은 담당자 승인이 필요합니다."]
}' | jq -e '[.data[].embedding | length] == [1024, 1024]'
```

## BGE Reranker v2 M3

검색 결과 상단에 덜 관련된 문서가 자주 나올 때 `BAAI/bge-reranker-v2-m3`를 추가합니다. `/v1/rerank`에 질문과 `documents` 배열을 보내고 `results[].index`를 받습니다. 앱은 `KNOWLEDGE_RERANK_URL` 뒤에 `/rerank`를 붙이므로 설정에는 `/v1`까지 입력합니다. [리랭커 모델 카드](https://huggingface.co/BAAI/bge-reranker-v2-m3) · [vLLM 재정렬 API](https://docs.vllm.ai/en/v0.28.0/models/pooling_models/scoring/#cohere-rerank-api)

```bash
# CPU 예시입니다. GPU를 쓸 경우 cpu를 GPU 번호로, 이미지 변수를 VLLM_IMAGE로 바꿉니다.
serve_model llm-rerank cpu 50304 "$VLLM_CPU_IMAGE" /models/bge-reranker-v2-m3 \
  --served-model-name bge-reranker-v2-m3 --runner pooling --dtype float32 \
  --max-model-len 8192 --max-num-seqs 4

model_curl "http://$MODEL_BIND:50304/v1/rerank" -H 'Content-Type: application/json' -d '{
  "model":"bge-reranker-v2-m3","query":"연차는 어디에서 신청하나요?",
  "documents":["식당은 정오에 운영합니다.","연차 신청은 사내 포털에서 합니다."],"top_n":2
}' | jq -e '.results | length == 2 and .[0].index == 1'
```

GPU에서 재정렬을 실행할 때는 `--dtype float32`를 빼고, 예를 들어 `--gpu-memory-utilization 0.20`부터 단독 시험합니다. 임베딩과 재정렬을 같은 GPU에서 운영하려면 각 서비스가 준비된 상태에서 동시에 요청해 최고 VRAM과 지연을 확인합니다.

## 벡터 저장소 선택

SQLite를 유지하면 추가 컨테이너 없이 `KNOWLEDGE_VECTOR_STORE=sqlite`로 의미 검색을 사용할 수 있습니다. Qdrant를 선택하면 [앱 연결 단계](06-app-connection.md#선택-qdrant)를 따릅니다. 임베딩 모델·리비전·벡터 저장소를 변경하면 기존 자료를 다시 색인하고, 알려진 문구와 의미가 비슷한 질문으로 각각 출처가 나오는지 확인합니다.
