"""Parse only calendar details that the user actually supplied."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from app.modules.assistant import gitlab


Repeat = Literal['once', 'daily', 'weekly']
Action = Literal['create', 'list', 'cancel']
MINUTE = 60
DAY = 24 * 60 * MINUTE
HORIZON = 180 * DAY

SCHEDULE = re.compile(r'예약|일정\s*(?:설정|등록)|\b(?:schedule[ds]?|remind|reminders?|recurring)\b', re.I)
LIST = re.compile(r'(?:예약(?:된|한)?\s*(?:작업|일정)?\s*(?:목록|보여\s*줘|보여\s*주세|조회|확인)|내\s*예약\s*작업|'
                  r'\b(?:list|show|see)\b[^.!?]{0,45}\b(?:schedules?|reminders?|scheduled\s+(?:tasks?|jobs?))\b)', re.I)
CANCEL = re.compile(r'취소|중지|멈춰|삭제|\b(?:cancel|stop|delete)\b', re.I)
CANCEL_SCHEDULE = re.compile(r'예약(?:된|한)?\s*(?:작업|일정)?\s*(?:을|를)?\s*(?:취소|중지|삭제)')
REPEAT = re.compile(r'(?P<daily>매일|\bdaily\b|\bevery\s+day\b)|(?P<weekly>매주|\bweekly\b|\bevery\s+week\b)', re.I)
UNSUPPORTED_REPEAT = re.compile(r'매월|매년|격주|주중|평일\s*마다|\b(?:monthly|yearly|weekdays|biweekly)\b|'
                                r'\bevery\s+(?:\d+|month|year|other)\b', re.I)
WEEKDAY = re.compile(r'(?P<ko>[월화수목금토일])요일|(?P<en>\bmonday\b|\btuesday\b|\bwednesday\b|\bthursday\b|\bfriday\b|\bsaturday\b|\bsunday\b)', re.I)
EVERY_WEEKDAY = re.compile(r'\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', re.I)
RELATIVE = re.compile(r'(?<!\d)(?P<ko>\d{1,4})\s*(?P<unit>분|시간|일)\s*(?:뒤|후)|'
                      r'\bin\s+(?P<en>\d{1,4})\s*(?P<english_unit>minutes?|hours?|days?)\b', re.I)
DATE = re.compile(r'(?<!\d)(?P<iso>\d{4}-\d{2}-\d{2})(?!\d)|(?P<relative>오늘|내일|모레|\btoday\b|\btomorrow\b)', re.I)
CLOCK = re.compile(r'(?<!\d)(?:(?P<meridian>오전|오후|am|pm)\s*)?(?:'
                   r'(?P<iso_hour>\d{1,2}):(?P<iso_minute>\d{2})(?::(?P<second>\d{2}))?(?P<offset>Z|[+-]\d{2}:\d{2})?'
                   r'|(?P<ko_hour>\d{1,2})\s*시(?!간)(?:\s*(?:(?P<ko_minute>\d{1,2})\s*분|(?P<half>반)))?'
                   r'|(?P<en_hour>\d{1,2})(?=\s*(?:am|pm)\b))'
                   r'(?:\s*(?P<suffix>am|pm)\b)?(?!\d)', re.I)
ZONE = re.compile(r'(?<![\w./:-])(?:[A-Za-z][A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)+|UTC)(?![\w./:-])')
DIRECT = re.compile(r'(?:해|보내|정리해|확인해|찾아|알려|읽어|분석해|요약해)\s*(?:줘|주세|줄래|주겠|주라)|(?:해|하)\s*주세요|해라|설정하라|'
                    r'(?:예약|등록|설정)\s*(?:해)?\s*(?:줘|주세요)|'
                    r'\b(?:please|(?:can|could|would)\s+you)\b|'
                    r'^\s*(?:schedule|remind|create|check|monitor|report|notify|send)\b', re.I)
DENIAL = re.compile(r'(?:예약|등록|설정|취소|중지)\s*(?:은|는|을|를)?\s*(?:하?지\s*말|하지\s*않|하지는\s*말|안\s*해|없이)|'
                    r'(?:해|보내|확인하|정리하|분석하|요약하|알리)\s*지\s*(?:말|마(?:[.!?\s]|$)|않)|'
                    r'\b(?:do\s+not|don[\'’]t|never|without)\s+(?:schedule|remind|cancel|stop|delete|check|report|set\s+up)\b', re.I)
INFORMATION = re.compile(r'\b(?:how\s+(?:to|do|can)|what\s+is\s+(?:a\s+)?(?:schedule|reminder)|for\s+example|translate|imagine|suppose|pretend)\b|'
                         r'사용법|(?:예약|일정).{0,28}(?:하는\s*(?:법|방법)|가능한가|가능해|할\s*수\s*있)|'
                         r'예시(?:로|를|가)?\s|(?:이|다음|아래)\s*(?:문장|문구).{0,40}(?:번역|설명|요약)', re.I)
MUTATION = re.compile(r'(?:푸시|푸쉬|커밋|배포|외부\s*(?:전송|발송)|메일\s*발송|메시지\s*전송|삭제)\s*'
                      r'(?:을|를)?\s*(?:해|하|후)|(?:PR|MR|풀\s*리퀘스트|머지\s*리퀘스트)\s*(?:을|를)?\s*(?:열|만들|생성)|'
                      r'(?:이메일|메일|슬랙|텔레그램|메시지)\s*(?:에|로|을|를)?\s*(?:보내|전송|발송)|'
                      r'\b(?:push|commit|deploy|delete|post|send\s+(?:an?\s+)?(?:e-?mail|message)|'
                      r'(?:create|open)\s+(?:an?\s+)?(?:PR|MR|pull\s+request|merge\s+request))\b', re.I)


@dataclass(frozen=True)
class Occurrence:
    repeat: Repeat
    time_zone: str
    timestamp: int
    local_minute: int


class MissingDetails(HTTPException):
    def __init__(self, detail):
        super().__init__(422, detail)


def clean(question):
    return gitlab.without_inline_quotes(gitlab.unquoted(question)).strip()


def action_for(question) -> Action | None:
    text = clean(question)
    if not text or DENIAL.search(text) or INFORMATION.search(text):
        return None
    repeated = REPEAT.search(text) or EVERY_WEEKDAY.search(text) or UNSUPPORTED_REPEAT.search(text)
    timed_text = gitlab.URL.sub(' ', text)
    dated = DATE.search(timed_text) or RELATIVE.search(timed_text) or WEEKDAY.search(timed_text)
    timed = dated and CLOCK.search(timed_text) or RELATIVE.search(timed_text)
    if (cancellation := CANCEL.search(text)) and (schedule := SCHEDULE.search(text)):
        cancel_request = re.match(r'\s*(?:해\s*(?:줘|주세|줄래)|하세요|해라|[.!?]*$)', text[cancellation.end():])
        targets_schedule = not (repeated or timed) or CANCEL_SCHEDULE.search(text)
        english_cancel = re.match(r'^(?:please\s+)?(?:cancel|stop|delete)\b', text, re.I)
        if targets_schedule and (cancellation.start() >= schedule.end() and cancel_request or english_cancel):
            return 'cancel'
    if LIST.search(text) and not (repeated or timed):
        return 'list'
    if DIRECT.search(text) and (SCHEDULE.search(text) or repeated or timed):
        return 'create'
    return None


def clarification_details(question):
    """A reply must add calendar values without introducing a second request."""
    text = clean(question).rstrip(' ,.!?')
    if not text or len(text) > 120 or DENIAL.search(text) or INFORMATION.search(text):
        return None
    text = re.sub(r'^(?:(?:네|응|그래|그럼)[,\s]+|please\s+)', '', text, flags=re.I)
    text = re.sub(r'\s*(?:으로|로|에)?\s*(?:(?:해|설정해|정해|등록해|예약해|부탁해)\s*'
                  r'(?:줘|주세(?:요)?|줄래)?|부탁(?:이야|드립니다))$', '', text).strip()
    patterns = (RELATIVE, DATE, REPEAT, WEEKDAY, CLOCK)
    if not any(pattern.search(text) for pattern in patterns):
        return None
    residue = text
    for pattern in patterns:
        residue = pattern.sub(' ', residue)
    residue = re.sub(r'한국\s*(?:시간|표준시)|\bKST\b|\b(?:at|on|and|in|every)\b|으로|에|로', '', ZONE.sub(' ', residue), flags=re.I)
    return text if not residue.strip(' ,.!?\t\n') else None


def zone_for(text, default):
    for value in ZONE.findall(gitlab.URL.sub(' ', text)):
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            # A repository path is not a time-zone request.
            if value.startswith(('Africa/', 'America/', 'Asia/', 'Atlantic/', 'Australia/', 'Europe/', 'Indian/', 'Pacific/')):
                raise HTTPException(422, 'IANA 시간대를 확인해 주세요. 예시는 Asia/Seoul입니다.') from None
    if re.search(r'한국\s*(?:시간|표준시)|\bKST\b', text):
        return ZoneInfo('Asia/Seoul')
    try:
        return ZoneInfo(default or 'UTC')
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, '계정 시간대가 유효하지 않습니다. Asia/Seoul처럼 시간대를 명시해 주세요.') from None


def local_timestamp(day, minute, zone):
    naive = datetime.combine(day, time(minute // 60, minute % 60))
    # At a clock rollback run once, on the earlier occurrence. At a forward jump use the same
    # minute after the gap; the next recurrence goes back to the requested wall time.
    return int(naive.replace(tzinfo=zone, fold=0).timestamp())


def next_recurrence(scheduled_at, repeat, time_zone, local_minute, now):
    zone = ZoneInfo(time_zone)
    previous = datetime.fromtimestamp(scheduled_at, zone).date()
    today = datetime.fromtimestamp(now, zone).date()
    interval = 1 if repeat == 'daily' else 7
    days = max(interval, ((today - previous).days // interval) * interval)
    candidate = local_timestamp(previous + timedelta(days=days), local_minute, zone)
    if candidate <= now:
        candidate = local_timestamp(previous + timedelta(days=days + interval), local_minute, zone)
    return candidate


def parse_occurrence(question, default_zone, now):
    text = clean(question)
    if MUTATION.search(text):
        raise HTTPException(422, '예약은 조회와 분석 결과를 앱의 대화에 남깁니다. 외부 변경은 실행할 때 별도로 요청해 주세요.')
    if UNSUPPORTED_REPEAT.search(text):
        raise HTTPException(422, '예약 반복 주기는 매일 또는 매주 한 요일로 지정해 주세요.')
    zone = zone_for(text, default_zone)
    current = datetime.fromtimestamp(now, zone)
    repeats = {match.lastgroup for match in REPEAT.finditer(text)}
    if EVERY_WEEKDAY.search(text):
        repeats.add('weekly')
    if len(repeats) > 1:
        raise HTTPException(422, '예약 반복 주기를 매일 또는 매주 중 하나로 알려 주세요.')
    repeat = next(iter(repeats), 'once')
    timed_text = gitlab.URL.sub(' ', text)
    offsets = list(RELATIVE.finditer(timed_text))
    dates = list(DATE.finditer(timed_text))
    clocks = list(CLOCK.finditer(timed_text))
    weekdays = {('월화수목금토일'.index(match['ko']) if match['ko'] else
                 ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday').index(match['en'].lower()))
                for match in WEEKDAY.finditer(timed_text)}
    if len(offsets) > 1 or len(dates) > 1 or len(clocks) > 1 or len(weekdays) > 1:
        raise HTTPException(422, '한 예약에 사용할 날짜와 시간을 하나로 알려 주세요.')
    if offsets:
        if repeat != 'once' or clocks or dates or weekdays:
            raise HTTPException(422, '몇 분 뒤에 실행할지 또는 정확한 날짜와 시간 중 하나를 알려 주세요.')
        offset = offsets[0]
        unit = (offset['unit'] or offset['english_unit']).lower().rstrip('s')
        seconds = int(offset['ko'] or offset['en']) * {'분': MINUTE, 'minute': MINUTE, '시간': 3600, 'hour': 3600, '일': DAY, 'day': DAY}[unit]
        if not MINUTE <= seconds <= HORIZON:
            raise HTTPException(422, '일회성 예약은 지금부터 1분 이상, 180일 이내로 설정할 수 있습니다.')
        local = datetime.fromtimestamp(now + seconds, zone)
        return Occurrence(repeat, zone.key, now + seconds, local.hour * 60 + local.minute)
    if not clocks:
        raise MissingDetails('실행할 시각을 알려 주세요. 예시는 내일 오전 9시, 매주 월요일 09:00입니다.')
    clock = clocks[0]
    hour = int(clock['iso_hour'] or clock['ko_hour'] or clock['en_hour'])
    minute = 30 if clock['half'] else int(clock['iso_minute'] or clock['ko_minute'] or 0)
    meridian = (clock['meridian'] or clock['suffix'] or '').lower()
    if ((meridian and not 1 <= hour <= 12) or hour > 23 or minute > 59 or
            (clock['second'] and clock['second'] != '00') or clock['meridian'] and clock['suffix']):
        raise HTTPException(422, '올바른 실행 시각을 분 단위로 알려 주세요.')
    if meridian:
        hour = hour % 12 + (12 if meridian in {'pm', '오후'} else 0)
    local_minute = hour * 60 + minute
    weekday = next(iter(weekdays), None)
    if repeat == 'weekly' and weekday is None:
        raise MissingDetails('매주 실행할 요일을 알려 주세요.')
    if repeat == 'daily' and weekday is not None:
        raise HTTPException(422, '매일 실행할지 특정 요일에 매주 실행할지 알려 주세요.')
    day = current.date()
    if dates:
        try:
            matched = dates[0]
            day = date.fromisoformat(matched['iso']) if matched['iso'] else day + timedelta(days={
                '오늘': 0, 'today': 0, '내일': 1, 'tomorrow': 1, '모레': 2}[matched['relative'].lower()])
        except ValueError:
            raise HTTPException(422, '달력에 존재하는 날짜를 알려 주세요.') from None
        if weekday is not None and day.weekday() != weekday:
            raise HTTPException(422, '지정한 날짜와 요일이 서로 다릅니다. 다시 확인해 주세요.')
    elif weekday is not None:
        day += timedelta(days=(weekday - current.weekday()) % 7)
    elif repeat == 'once':
        raise MissingDetails('일회성 예약을 실행할 날짜를 알려 주세요. 예시는 내일, 2026-12-01입니다.')
    if offset := clock['offset']:
        if repeat != 'once' or not dates or not dates[0]['iso'] or meridian:
            raise HTTPException(422, 'UTC 오프셋은 일회성 예약의 날짜와 함께 적어 주세요. 반복 예약에는 IANA 시간대를 사용합니다.')
        try:
            if offset != 'Z' and int(offset[-2:]) > 59:
                raise ValueError('offset minutes')
            timestamp = int(datetime.fromisoformat(f'{day.isoformat()}T{hour:02d}:{minute:02d}:00{offset.upper()}').timestamp())
        except ValueError:
            raise HTTPException(422, '올바른 UTC 오프셋을 적어 주세요.') from None
    else:
        timestamp = local_timestamp(day, local_minute, zone)
    if timestamp < now + MINUTE and not dates:
        timestamp = local_timestamp(day + timedelta(days=7 if weekday is not None else 1), local_minute, zone)
    if not now + MINUTE <= timestamp <= now + HORIZON:
        raise HTTPException(422, '실행 시각은 지금부터 1분 이상, 180일 이내여야 합니다.')
    return Occurrence(repeat, zone.key, timestamp, local_minute)
