# ADR-20260704-semantic-embedding-load-contract — palimpsest 의미층 임베딩 적재 계약: provider-free, 인덱스당 단일 model·차원 pin·독립 코사인-KNN 회상 채널

- 식별자: `ADR-20260704-semantic-embedding-load-contract` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-04
- work item: wi_260703qov (B-1 — 의미층 임베딩 슬라이스, 커밋 431c591)
- 관계: `ADR-20260701-semantic-layer-load-contract`의 **임베딩 축 확장**이다 — 그 ADR을 supersede하지 않는다(계속 active, 본 ADR이 후속을 구체화). slice 4가 세운 Summary 노드 + SUMMARIZES(inferred) 적재 계약에 임베딩을 부착하고 벡터 회상 채널을 여는 것이며, provider-free 불변식은 그대로 계승한다. branch 앵커는 `ADR-20260703-branch-scoped-node-identity`를 준수한다.

## 맥락

slice 4(ADR-20260701)는 Summary 노드 + SUMMARIZES(inferred) 적재 계약을 세웠으나, 그 위의 회상은 **구조적 채널(그래프 순회)** 뿐이었다 — 의미적으로 가까운 요약을 유사도로 찾는 경로가 없었다. B-1(wi_260703qov)은 그 Summary에 **임베딩을 부착**하고 **벡터-KNN 회상 채널**을 열되, ADR-20260701의 provider-free 불변식(palimpsest가 인코더를 직접 호출하지 않음)을 깨지 않는다. 즉 벡터의 생산은 외부 인코더의 책임으로 두고, palimpsest는 적재·인덱스·회상만 맡는다.

## 결정

palimpsest 의미층 임베딩 적재·회상 계약을 다음으로 결정한다.

1. **임베딩은 Summary 적재 계약의 선택적 확장(임베딩 축).** Summary 노드가 `embedding`/`embedding_model`/`embedding_dim` 3필드를 실을 수 있다(`src/palimpsest/kg/summary.py:92-94`). 임베딩이 있으면 `embedding_model`이 non-empty여야 하며, 없으면 "embedding without embedding_model"로 거부한다(`summary.py:135-136`). 임베딩 없는 Summary는 그대로 유효 — 하위호환(slice 4 계약을 깨지 않음).
2. **인덱스당 단일 embedding_model 불변식(load-bearing provenance).** 그래프에 이미 결박된 embedding_model이 있으면(`_established_embedding_model`, `summary.py:295-303`), 다른 model의 임베딩은 **차원이 같아도 거부**한다(`summary.py:325-339`). 서로 다른 모델의 임베딩 공간을 한 인덱스에 섞으면 코사인 유사도가 무의미해지기 때문 — embedding_model은 장식이 아니라 부하를 지는(load-bearing) provenance다.
3. **차원 1536을 VECTOR INDEX DDL에 pin(비가역).** `EMBEDDING_DIM`(=1536) 단일 상수가 적재 시 차원 검증(`summary.py:127-133`)과 인덱스 DDL(`summary.py:241-244` — `CREATE VECTOR INDEX summary_embedding_cosine FOR (s:Summary) ON (s.embedding)`, `cosine`, `vector.dimensions`)을 **동시에** 지배한다. 차원 변경은 인덱스 재빌드를 요구하는 **비가역 변경**이다.
4. **recall_semantic = 독립 코사인-KNN 회상 채널.** `recall_semantic(driver, query_vector, branches, limit)`(`src/palimpsest/recall/graphrag.py:870`)는 호출자가 준 `EMBEDDING_DIM` 벡터(길이·finite·NaN/inf 검증 `graphrag.py:834-846`)로 `db.index.vector.queryNodes` KNN을 돌려, summaries 채널과 **동일한 bounded 결과 형태**(`{items, sources, summaries, ...}`)를 반환한다. **branch 앵커**: `$branches` 필터(null=전 평면), `tgt.branch` — ADR-20260703 준수. 하이브리드(벡터→그래프 순회)는 유예하고, 이번엔 독립 KNN만 연다.
5. **score 노출 계약 + provider-free 유지.** `score`는 인덱스의 코사인 유사도로, 결과의 confidence와 **분리** 유지된다(`graphrag.py:879,919`). 정규화 `(1+cos)/2`, 음수 코사인은 0.5로 clamp(`graphrag.py:820-821`), `_MIN_COSINE_SCORE=0.6` 미만 히트는 드롭해 낮은 유사도로 k를 채우지 않는다(confident-empty 우선, `graphrag.py:824,910`). palimpsest는 임베딩 모델을 직접 호출하지 않는다 — 외부 인코더가 벡터를 생산하고 query_vector도 호출자가 공급하며, palimpsest는 적재·인덱스·회상만 한다(provider-free).

## 근거 (rationale)

- **provider-free 계승**: ADR-20260701이 요약 생성을 외부에 둔 것과 같은 이유로, 벡터 생산도 외부 인코더에 둔다 — 코드에 provider·키·네트워크를 박지 않아 테스트가 hermetic하게 재현된다. query_vector조차 호출자가 공급해 palimpsest는 벡터를 생산하지 않는다.
- **단일-model·차원 pin**은 임의 제약이 아니라 코사인 유사도가 의미를 가지기 위한 전제다. 이질적 임베딩 공간을 한 인덱스에 섞으면 score가 거짓이 되므로, embedding_model provenance를 부하 지는 검증 대상으로 승격했다.
- **독립 KNN 우선, 하이브리드 유예**: slice 4가 구조 채널만 열었던 것과 대칭으로, 이번은 벡터 채널만 최소로 열고 벡터→그래프 순회 융합은 후속으로 미룬다(deferred, not forbidden).
- **score와 confidence 분리**는 ADR-20260701이 세운 "생성기 confidence와 별개 필드" 규율의 연장이다 — 유사도(회상 신호)와 요약 자체의 신뢰도를 섞지 않는다.

## stale (honesty)

임베딩된 Summary도 기존 stale 계약(코드-결박 신선도)을 그대로 받는다 — 대상 코드가 재ingest로 갱신되면 회상이 `stale=true`를 노출한다. 다만 **임베딩 자체의 재생성**은 외부 인코더가 필요하므로 provider-free 경계 밖이다(palimpsest가 자동 재생성하지 않음). 즉 요약 텍스트·근거의 신선도는 추적하되, 벡터의 재계산은 외부 책임으로 남는다.

## 철회·변경 조건 (change_condition)

다음 시 재검토한다.

- (a) **하이브리드 벡터→그래프 순회** 도입 시(독립 KNN을 넘어 벡터 히트를 그래프 순회의 seed로 융합).
- (b) **다중모델·다중차원 인덱스** 필요 시(인덱스당 단일-model 불변식(결정 2)과 차원 pin(결정 3)을 완화).
- (c) **query-vector 생산 어댑터** 추가 시(palimpsest가 벡터를 생산하게 되면 provider-free 경계(결정 5)를 재검토).
- (d) **임베딩 대상 확장** 시(CommunityReport 등 Summary 외 노드로 임베딩·벡터 인덱스를 넓힘).
