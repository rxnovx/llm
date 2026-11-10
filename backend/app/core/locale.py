from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_time_zone(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Use a valid IANA time zone, for example Asia/Seoul or America/New_York.") from error
    return value
