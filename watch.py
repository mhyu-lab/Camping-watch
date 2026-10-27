"""오산 맑은터공원 캠핑장 빈자리 감시 → ntfy 알림."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://forest.maketicket.co.kr/event/event.do?command=event_view_m_area_info"
BOOKING_URL = "https://forest.maketicket.co.kr/ticket/GD82"
IDKEY = "5M4220"
GD_SEQ = "GD82"

DATES = ["20261031"]
ZONES = ["까산이존"]

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
# 감시 현황(status.json)을 올릴 브랜치와 주기(초). 웹페이지(docs/index.html)가 읽는다.
STATUS_BRANCH = "status"
STATUS_EVERY = 60
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

KST = timezone(timedelta(hours=9))
# f_SelectDateZone( '20261031', 'CM000079' , 'SD69032' , '0'); ... <label for="area1">까산이존(0)</label>
ZONE_RE = re.compile(
    r"f_SelectDateZone\(\s*'(\d{8})',\s*'(\w+)'\s*,\s*'(\w+)'\s*,\s*'(-?\d+)'\).*?<label[^>]*>([^<(]+)\(",
    re.S,
)


def fetch_zones(date):
    data = urllib.parse.urlencode(
        {"idkey": IDKEY, "gd_seq": GD_SEQ, "sd_date": date, "view_type": "new"}
    ).encode()
    req = urllib.request.Request(
        URL, data=data, headers={"User-Agent": "Mozilla/5.0 (camping-watch)"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", "replace")
    return {name.strip(): int(cnt) for _, _, _, cnt, name in ZONE_RE.findall(html)}


def notify(title, message):
    print(f"[알림] {title}: {message}")
    if not NTFY_TOPIC:
        return
    # 한글 제목은 헤더 대신 쿼리 파라미터로 보낸다
    params = urllib.parse.urlencode(
        {"title": title, "priority": "high", "tags": "tent", "click": BOOKING_URL}
    )
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}?{params}", data=message.encode()
    )
    urllib.request.urlopen(req, timeout=20).read()


def github_api(method, path, body):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}{path}",
        data=json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def publish_status(status):
    """status.json 하나만 든 커밋으로 status 브랜치를 덮어쓴다 (기록이 쌓이지 않게)."""
    if not (GITHUB_TOKEN and GITHUB_REPOSITORY):
        return
    content = json.dumps(status, ensure_ascii=False, indent=1)
    try:
        tree = github_api("POST", "/git/trees", {"tree": [
            {"path": "status.json", "mode": "100644", "type": "blob", "content": content}
        ]})
        commit = github_api("POST", "/git/commits", {
            "message": "감시 현황", "tree": tree["sha"], "parents": []
        })
        try:
            github_api("PATCH", f"/git/refs/heads/{STATUS_BRANCH}",
                       {"sha": commit["sha"], "force": True})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 브랜치가 아직 없으면 422
                raise
            github_api("POST", "/git/refs",
                       {"ref": f"refs/heads/{STATUS_BRANCH}", "sha": commit["sha"]})
    except Exception as e:
        print(f"현황 업로드 실패: {e}", file=sys.stderr)


def now_iso():
    return datetime.now(KST).isoformat(timespec="seconds")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def check(prev):
    """한 번 조회해서 새 상태를 돌려준다. 모든 날짜 조회에 실패하면 None."""
    today = datetime.now(KST).strftime("%Y%m%d")
    state = {}
    errors = 0

    for date in DATES:
        if date < today:
            continue
        try:
            zones = fetch_zones(date)
        except Exception as e:
            print(f"{date}: 조회 실패 {e}", file=sys.stderr)
            errors += 1
            continue
        for zone in ZONES:
            key = f"{date}/{zone}"
            cnt = zones.get(zone)
            print(f"{datetime.now(KST):%H:%M:%S} {key}: {cnt}", flush=True)
            if cnt is None:
                continue
            state[key] = cnt
            # 0 → 1 이상으로 바뀌었거나 자리가 더 늘었을 때만 알림
            if cnt > 0 and cnt > prev.get(key, 0):
                d = datetime.strptime(date, "%Y%m%d")
                label = f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})"
                msg = f"{label} {zone} 잔여 {cnt}. 지금 예약하세요."
                notify(f"{zone} 빈자리 {cnt}개!", msg)
                state["_last_alert"] = {"at": now_iso(), "message": msg}

    if errors and errors == len([d for d in DATES if d >= today]):
        return None
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def main():
    if os.environ.get("TEST_NOTIFY"):
        if not NTFY_TOPIC:
            sys.exit("NTFY_TOPIC Secret이 비어 있어요.")
        notify("캠핑장 감시 테스트", "이 알림이 보이면 설정 완료!")
        return

    # INTERVAL초마다 DURATION초 동안 반복 (DURATION=0이면 한 번만)
    interval = int(os.environ.get("INTERVAL", "10"))
    duration = int(os.environ.get("DURATION", "0"))
    end = time.monotonic() + duration
    state = load_state()
    fails = 0
    # 누적 횟수는 state.json에 남겨 다음 실행으로 이어진다
    stats = state.setdefault("_stats", {"total": 0, "failed": 0, "since": now_iso()})
    run = {"checks": 0, "started_at": now_iso()}
    last_publish = 0.0

    def status(state_name):
        today = datetime.now(KST).strftime("%Y%m%d")
        return {
            "state": state_name,
            "updated_at": now_iso(),
            "interval": interval,
            "total_checks": stats["total"],
            "failed_checks": stats["failed"],
            "since": stats["since"],
            "run": run,
            "consecutive_fails": fails,
            "zones": ZONES,
            "results": {
                d: {z: state.get(f"{d}/{z}") for z in ZONES} for d in DATES if d >= today
            },
            "last_alert": state.get("_last_alert"),
        }

    while True:
        if max(DATES) < datetime.now(KST).strftime("%Y%m%d"):
            print("감시할 날짜가 모두 지났어요.")
            publish_status(status("finished"))
            # 워크플로가 다음 감시를 이어서 시작하지 않도록 알린다
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write("done=true\n")
            break
        new = check(state)
        stats["total"] += 1
        run["checks"] += 1
        if new is None:
            fails += 1
            stats["failed"] += 1
            # 연속 실패가 길어지면(차단 등) 알림 한 번
            if fails == 30:
                notify("캠핑장 감시 오류", "예약 사이트 조회가 계속 실패하고 있어요.")
        else:
            fails = 0
            state.update(new)
        save_state(state)
        if time.monotonic() - last_publish >= STATUS_EVERY:
            publish_status(status("running"))
            last_publish = time.monotonic()
        if time.monotonic() + interval >= end:
            break
        time.sleep(interval)

    if duration:
        publish_status(status("handover"))  # 다음 실행으로 넘어가는 중

    if fails and duration == 0:
        sys.exit("모든 날짜 조회 실패")


if __name__ == "__main__":
    main()
