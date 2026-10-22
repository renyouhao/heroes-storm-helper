from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def now():
    return datetime.now(BEIJING)


def month_key(value):
    if value.tzinfo is None:
        raise ValueError('时间必须带时区')
    return value.astimezone(BEIJING).strftime('%Y-%m')
