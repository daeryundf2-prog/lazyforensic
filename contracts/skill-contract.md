# 스킬 계약 틀

> 출처: hypercore `skills/hyper-loop/SKILL.md`의 instruction_contract 표와 활성화 예시 구조 (MIT, Copyright (c) 2026 alpoxdev,
> pinned `2d305cbf5db72e337189aa584b363bb9ced2d9bc`). 9행 표를 이 저장소 용도에 맞게 4행으로 줄이고 문구는 새로 썼다. 표기는 `NOTICE`.

각 `skills/*/SKILL.md` 끝에 아래 절을 둔다. 프론트매터의 `description`(트리거·쓰지 말 것)과 겹치지 않게 짧게 쓴다.

```markdown
## 계약

| 항목 | 내용 |
|---|---|
| Intent | 이 스킬이 끝내는 한 가지 일 |
| Scope | 하는 일과 하지 않는 일의 경계 |
| Evidence | 결과가 기대는 증거와 신뢰 등급(contracts/trust-levels.md) |
| Stop | 끝났다고 말해도 되는 관측 가능한 조건 |

### 활성화 예시

- 긍정: 이 스킬을 써야 하는 요청
- 부정: 쓰면 안 되는 요청
- 경계: 애매한 요청과 그때의 행동
```

2열 표만 쓴다(markdown_structure_guard가 행 열 수를 검사한다).
