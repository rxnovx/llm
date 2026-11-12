# 폐쇄망 Docker 모델 설치 매뉴얼

기준 환경은 Linux x86-64, NVIDIA H100, Docker Engine과 NVIDIA Container Toolkit입니다. 외부망 준비 PC에서 이미지와 체크포인트를 내려받아 검증한 뒤, 폐쇄망 H100 서버로 반입합니다. FastAPI는 모델 서버에 HTTP로 연결합니다. Termux에서 앱을 실행한다면 H100 서버의 사설 주소를 사용합니다.

## 시작 순서

1. [이미지·모델 반입과 GPU 확인](01-offline-transfer.md)
2. [채팅 LLM과 첨부 이미지 분석](02-chat-and-vision.md)
3. [임베딩과 검색 결과 재정렬](03-search.md)
4. [이미지 생성과 편집](04-images.md)
5. [한국어 낭독과 음악·효과음](05-audio.md)
6. [앱 연결과 동작 확인](06-app-connection.md)

`google/gemma-4-31B-it` 한 개로 채팅·도구 호출·첨부 이미지 분석을 시작합니다. Office·PDF 추출과 PPTX 생성은 백엔드에서 처리하고, 스캔 PDF는 Tesseract로 읽습니다. 의미 검색이 필요할 때 임베딩을 추가하고 이후 필요한 기능만 연결합니다. [Gemma 4 모델 카드](https://ai.google.dev/gemma/docs/core/model_card_4) · [파일·지식 설정](../../ASSISTANT.md) · [결과 파일 형식](../../USER_WORKFLOWS.md#결과-파일의-범위)

## 추천 모델

| 용도 | 모델 | 선택 이유 | 예시 호스트 포트 |
| --- | --- | --- | ---: |
| 채팅·도구 호출·문서 생성·이미지 분석 | `google/gemma-4-31B-it` | 한 서버에서 텍스트와 이미지 입력을 처리합니다. 공식 BF16 서빙 예시에 맞춰 H100 두 장으로 시작합니다. | 50301 |
| 문서 의미 검색 | `BAAI/bge-m3` | 한국어와 영어가 섞인 문서용 다국어 dense 임베딩입니다. | 50303 |
| 검색 결과 재정렬 | `BAAI/bge-reranker-v2-m3` | 검색 후보와 질문의 관련도를 다시 평가합니다. | 50304 |
| 이미지 생성 | `black-forest-labs/FLUX.1-schnell` | Apache 2.0의 텍스트→이미지 모델입니다. | 50305 |
| 이미지 편집 | `black-forest-labs/FLUX.1-Kontext-dev` | 이미지 한 장과 편집 지시를 받습니다. 가중치 사용 조건을 확인한 환경에서 선택합니다. | 50306 |
| 한국어 WAV 낭독 | `openbmb/VoxCPM2` | 한국어를 지원하고 기본 음성으로 낭독을 시험할 수 있습니다. | 50307 |
| 짧은 음악·효과음 | `stabilityai/stable-audio-open-1.0` | 배경음·효과음에 적합합니다. 사내 사용 조건을 확인한 환경에서 선택합니다. | 50308 |

이미지 생성·편집을 한 서버로 합칠 경우 `black-forest-labs/FLUX.2-klein-4B`도 사용할 수 있습니다. 이 체크포인트는 내부에 **Qwen3 텍스트 인코더**를 포함하므로 반입 기준에 맞는 경우에 선택합니다. [이미지 모델별 구성](04-images.md) · [vLLM-Omni 지원 모델](https://docs.vllm.ai/projects/vllm-omni/en/v0.28.0/models/supported_models/)

## H100 4장 배치 예시

| GPU | 처음 올릴 서비스 | 운영할 때 확인할 것 |
| --- | --- | --- |
| GPU 0·1 | `google/gemma-4-31B-it` BF16, TP 2 | 16K 문맥과 동시 시퀀스 4개로 시작해 실제 대화 길이와 이미지 첨부에서 응답 시간과 KV 캐시 여유를 측정합니다. |
| GPU 2 | 이미지 생성 또는 편집 | 단일 모델로 합치거나 두 서버의 최대 VRAM을 각각 측정한 뒤 배치합니다. |
| GPU 3 | TTS 또는 음악·효과음 | 단독 실행 후 두 서비스를 같은 카드에서 띄울 수 있는지 확인합니다. |
| CPU 또는 별도 GPU | 임베딩·리랭커 | 검색·색인 지연을 확인하고 CPU 또는 여유 GPU를 선택합니다. |

GPU 번호는 예시입니다. 이미 실행 중인 컨테이너가 있으면 [GPU 점유 확인](01-offline-transfer.md#폐쇄망에서-가져오기) 후 비어 있는 GPU 번호나 UUID를 지정합니다. 서비스가 모두 필요한 경우 각 모델의 최고 VRAM과 동시 요청 수를 재고 GPU를 공유하거나 별도 호스트에 배치합니다.
