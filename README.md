# Camping-watch
캠핑장 빈자리 찾기

오산 맑은터공원(https://forest.maketicket.co.kr/ticket/GD82) · 수원 광교호수공원(https://forest.maketicket.co.kr/ticket/GD129) · 의왕 왕송호수 캠핑장에 취소분이 생기면 ntfy로 알림을 보낸다.

- 감시 날짜/구역: `watch.py`의 `DATES`/`ZONES`(맑은터), `GG_DATES`/`GG_ZONES`(광교), `UUC_DATES`/`UUC_TYPES`(의왕)
- 실행: GitHub Actions가 5분마다 `watch.py` 실행
- 알림: 저장소 Secret `NTFY_TOPIC`에 ntfy 주제 이름을 넣으면 그 주제로 전송
- 잔여 수가 0에서 늘어날 때만 알림 (같은 빈자리로 반복 알림 안 함)
